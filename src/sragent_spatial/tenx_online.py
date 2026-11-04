"""Discover public 10x dataset cards and validate published Visium HD outputs."""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .hd_archive import spatial_bin_size, validate_hd_bin

CATALOG = "https://www.10xgenomics.com/datasets"
MIB = 1024**2
RANGE_BYTES = 8 * MIB
CDN = "cf.10xgenomics.com"
SPECIES = {"homo sapiens": "Human", "mus musculus": "Mouse", "rattus norvegicus": "Rat"}


def platform_for(product, title):
    text = (product + " " + title).lower()
    if "visium hd" in text or "hd spatial gene expression" in text:
        return "Visium HD"
    if "xenium" in text:
        return "Xenium"
    if "visium" in text:
        return "Visium"
    return None


class CatalogAccessError(RuntimeError):
    pass


def open_catalog_page(page, url, *, interactive=False):
    response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    if response is None or response.status < 400:
        return
    if response.status in (403, 429) and response.headers.get("x-vercel-mitigated") == "challenge":
        from playwright.sync_api import TimeoutError

        if interactive:
            print("10x requests browser verification. Complete it in the Chrome window opened by "
                  "this command. The command will continue when the 10x page loads (up to 5 minutes).",
                  file=sys.stderr, flush=True)
        try:
            outcome = page.wait_for_function(
                r"""expectedUrl => {
                    const expected = new URL(expectedUrl);
                    if (document.title.includes('Vercel Security Checkpoint')
                        && /failed to verify your browser|无法验证您的浏览器/i.test(document.body?.innerText ?? '')
                        && /(?:code|代码)\s*10\b/i.test(document.body?.innerText ?? '')) {
                        return 'denied';
                    }
                    return (location.origin === expected.origin
                        && location.pathname.replace(/^\/(?:cn|jp)(?=\/)/, '') === expected.pathname
                        && new URLSearchParams(location.search).get('search') === expected.searchParams.get('search')
                        && document.title.includes('10x Genomics')
                        && !document.title.includes('Vercel Security Checkpoint')) ? 'loaded' : false;
                }""",
                arg=url,
                timeout=300_000 if interactive else 15_000,
            ).json_value()
            if outcome == "denied":
                raise CatalogAccessError(f"10x returned HTTP {response.status}; Vercel denied browser verification (code 10)")
            return
        except TimeoutError as exc:
            raise CatalogAccessError(
                f"10x returned HTTP {response.status} with an unresolved browser verification page"
            ) from exc
    raise CatalogAccessError(f"10x returned HTTP {response.status}")


def read_catalog_snapshot(path, organism, tissue, platforms, limit):
    species = SPECIES.get(organism.casefold(), organism)
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))["samples"]
        if not isinstance(rows, list):
            raise ValueError("Expected a list of catalog records")
        matches = []
        seen = set()
        for row in rows:
            if (row["species"].casefold() != species.casefold() or row["tissue"].casefold() != tissue.casefold()
                    or row["platform"] not in platforms):
                continue
            slug = row["sample_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", slug) or row["source_url"] != f"{CATALOG}/{slug}":
                raise ValueError("Invalid official 10x dataset URL")
            for kind, urls in row["archives"].items():
                if kind not in {"binned_outputs", "spatial"} or not isinstance(urls, list):
                    raise ValueError("Invalid official 10x archive list")
                for url in urls:
                    if not re.fullmatch(
                        rf"https://cf\.10xgenomics\.com/samples/spatial-exp/[\w.-]+/[\w-]+/[\w-]+_{kind}\.tar\.gz", url
                    ):
                        raise ValueError("Invalid official 10x archive URL")
            if slug not in seen:
                matches.append(row)
                seen.add(slug)
        return matches[:limit]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CatalogAccessError(f"Invalid 10x catalog snapshot: {exc}") from exc


def discover_10x(organism, tissue, platforms, limit, *, interactive=False):
    from playwright.sync_api import Error

    try:
        return _discover_10x(organism, tissue, platforms, limit, interactive=interactive)
    except Error as exc:
        raise CatalogAccessError(
            f"The 10x catalog browser could not read the public page ({type(exc).__name__})"
        ) from exc


def _discover_10x(organism, tissue, platforms, limit, *, interactive=False):
    from playwright.sync_api import sync_playwright

    species = SPECIES.get(organism.casefold(), organism)
    results = []
    with sync_playwright() as playwright:
        with playwright.chromium.launch(channel="chrome", headless=False) as browser:
            page = browser.new_page()
            for platform in platforms:
                query = urlencode({"search": f"{platform} {species} {tissue}", "sort": "publishedAt DESC"})
                open_catalog_page(page, f"{CATALOG}?{query}", interactive=interactive)
                cards = page.locator('a[href^="/datasets/"]').filter(has_text="Sample type")
                page.wait_for_function("""() =>
                    [...document.querySelectorAll('a[href^="/datasets/"]')].some(a => a.innerText.includes('Sample type'))
                    || /no (datasets|results) found/i.test(document.body.innerText)""", timeout=90_000)
                matches = []
                for card in cards.all():
                    lines = card.inner_text().splitlines()
                    href = card.get_attribute("href") or ""
                    if not re.fullmatch(r"/datasets/[A-Za-z0-9_-]+", href) or len(lines) < 6:
                        continue
                    if lines[2] != "Species" or lines[3].casefold() != species.casefold() or lines[4] != "Sample type":
                        continue
                    if lines[5].casefold() != tissue.casefold() or platform_for(*lines[:2]) != platform:
                        continue
                    slug = href.rsplit("/", 1)[-1]
                    if not any(row["sample_id"] == slug for row in matches):
                        matches.append({"sample_id": slug, "platform": platform, "title": lines[1],
                                        "source_url": urljoin(CATALOG, href), "species": species,
                                        "tissue": lines[5], "catalog_query": query})
                for row in matches[:limit]:
                    try:
                        open_catalog_page(page, row["source_url"], interactive=interactive)
                        page.get_by_role("button", name="Output and supplemental files").click(timeout=30_000)
                        row["archives"] = {}
                        for link in page.get_by_role("link", name=re.compile("Binned outputs|Spatial outputs", re.I)).all():
                            url = link.get_attribute("href") or ""
                            parts = urlsplit(url)
                            if parts.scheme == "https" and parts.netloc == CDN and not parts.query and not parts.fragment:
                                for kind in ("binned_outputs", "spatial"):
                                    if parts.path.endswith(f"_{kind}.tar.gz"):
                                        row["archives"].setdefault(kind, [])
                                        if url not in row["archives"][kind]:
                                            row["archives"][kind].append(url)
                    except Exception as exc:
                        row["catalog_error"] = type(exc).__name__
                        if isinstance(exc, CatalogAccessError):
                            row["catalog_reason"] = str(exc)
                    results.append(row)
    return results


class ArchiveBudgetExceeded(ValueError):
    pass


class ArchiveStream(io.RawIOBase):
    """Read a bounded sequential prefix using verified HTTP ranges."""
    def __init__(self, url, budget, cache):
        self.url = url
        self.budget = budget
        self.offset = 0
        self.size = None
        self.pending = memoryview(b"")
        self.cache = cache / hashlib.sha256(url.encode()).hexdigest()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.identity = None
        self.downloaded = 0
        self.reused = 0

    def range_bytes(self, end):
        mirror = self.url.replace("https://cf.10xgenomics.com/", "https://s3-us-west-2.amazonaws.com/10x.files/", 1)
        path = self.cache / f"{self.offset}.range"
        try:
            with path.open("rb") as saved:
                identity, digest = json.loads(saved.readline(4096))
                data = saved.read(RANGE_BYTES + 1)
            if (identity[0] in (mirror, self.url) and identity[1].startswith('"')
                    and (self.identity is None or self.identity == identity)
                    and data
                    and len(data) == min(end + 1, identity[2]) - self.offset
                    and hashlib.sha256(data).hexdigest() == digest):
                self.identity = identity
                self.reused += len(data)
                return data
        except (OSError, ValueError, TypeError, IndexError, KeyError, AttributeError):
            pass
        span = f"{self.offset}-{end}"
        sources = (self.identity[0],) if self.identity else (mirror, self.url)
        for url in sources:
            try:
                headers = {"Range": f"bytes={span}", "Accept-Encoding": "identity"}
                if self.identity and self.identity[1]:
                    headers["If-Match"] = self.identity[1]
                with urlopen(Request(f"{url}?sragent_range={span}", headers=headers), timeout=25) as response:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if response.status != 206 or not match:
                        raise OSError("10x did not return an exact archive range")
                    start, actual_end, size = map(int, match.groups())
                    etag = response.headers.get("ETag", "")
                    identity = [url, etag if etag.startswith('"') else "", size]
                    if (start != self.offset or actual_end != min(end, size - 1)
                            or (self.identity is not None and identity != self.identity)):
                        raise OSError("10x archive range or object version changed")
                    expected = actual_end - start + 1
                    data = response.read(expected)
                    if len(data) != expected:
                        raise OSError("Truncated archive range")
            except (OSError, HTTPException) as exc:
                error = exc
                continue
            if identity[1]:
                temporary = path.with_suffix(".partial")
                with temporary.open("wb") as saved:
                    saved.write(json.dumps([identity, hashlib.sha256(data).hexdigest()]).encode() + b"\n")
                    saved.write(data)
                    saved.flush()
                    os.fsync(saved.fileno())
                temporary.replace(path)
            self.identity = identity
            self.downloaded += len(data)
            return data
        raise OSError(f"Unable to read 10x archive range {span}") from error

    def read(self, length):
        while len(self.pending) < length and (self.size is None or self.offset < self.size):
            end = min(self.offset + RANGE_BYTES, self.budget, self.size or self.budget) - 1
            if end < self.offset:
                raise ArchiveBudgetExceeded("10x archive exceeded compressed byte budget")
            data = self.range_bytes(end)
            self.size = self.identity[2]
            self.offset += len(data)
            self.pending = memoryview(self.pending.tobytes() + data) if self.pending else memoryview(data)
        data, self.pending = self.pending[:length].tobytes(), self.pending[length:]
        return data


SPATIAL = {"tissue_positions.parquet": "positions", "scalefactors_json.json": "scalefactors",
           "tissue_lowres_image.png": "image"}
EXPRESSION = {"matrix.mtx.gz": "matrix", "barcodes.tsv.gz": "barcodes"}


def collect(stream, out):
    files = {}
    required = set((SPATIAL | EXPRESSION).values())
    prefix = "binned_outputs/square_002um/"
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if not name.startswith(prefix):
                if files and re.match(r"binned_outputs/square_(?!002)\d+um(?:/|$)", name):
                    break
                continue
            names = name[len(prefix):].split("/")
            if len(names) != 2:
                continue
            section, filename = names
            role = (EXPRESSION.get(filename) if section == "filtered_feature_bc_matrix"
                    else SPATIAL.get(filename) if section == "spatial" else None)
            if not role:
                continue
            if role in files or not member.isfile() or member.size > (512 * MIB if role != "matrix" else 4 * 1024 * MIB):
                raise ValueError("Unexpected 10x archive member")
            path = out / Path(name).name
            with archive.extractfile(member) as source, path.open("wb") as target:
                if role == "matrix":
                    target.write(source.read(65536))
                else:
                    shutil.copyfileobj(source, target, MIB)
            files[role] = {"path": path, "member": name}
            if set(files) == required:
                break
    return files


def validate_online(row, out, budget):
    result = {**row, "source": "10x_online", "found": True, "selected": True, "files_ready": False}
    if row["platform"] != "Visium HD":
        return {**result, "status": "unsupported_online_validation"}
    if row.get("catalog_error"):
        return {**result, "status": "access_failed"}
    archives = row.get("archives", {})
    binned = archives.get("binned_outputs", [])
    if len(binned) != 1:
        return {**result, "status": "ambiguous_bundle" if binned else "missing_files"}
    if not budget:
        return {**result, "status": "download_disabled"}
    out.mkdir(parents=True, exist_ok=True)
    stream = None
    try:
        stream = ArchiveStream(binned[0], budget, out / ".archive-cache")
        files = collect(stream, out)
        measured = validate_hd_bin(files, out, 2)
        result.update(status=measured["status"], files_ready=measured["status"] != "missing_files",
                      bins_um={"2": measured})
    except ArchiveBudgetExceeded as exc:
        result.update(status="download_budget_exceeded", reason=str(exc))
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        result.update(status="processing_failed", error_type=type(exc).__name__, reason=str(exc))
    if stream is not None:
        result.update(archive_bytes_read=stream.offset, downloaded_bytes=stream.downloaded,
                      reused_bytes=stream.reused)
    return result
