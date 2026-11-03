"""Discover public 10x dataset cards and validate published Visium HD outputs."""
import io
import re
import shutil
import tarfile
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .hd_archive import spatial_bin_size, validate_hd_bin

CATALOG = "https://www.10xgenomics.com/datasets"
MIB = 1024**2
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


def discover_10x(organism, tissue, platforms, limit):
    from playwright.sync_api import Error

    try:
        return _discover_10x(organism, tissue, platforms, limit)
    except Error as exc:
        raise RuntimeError("The 10x catalog browser could not read the public page") from exc


def _discover_10x(organism, tissue, platforms, limit):
    from playwright.sync_api import sync_playwright

    species = SPECIES.get(organism.casefold(), organism)
    results = []
    with sync_playwright() as playwright:
        with playwright.chromium.launch(channel="chrome", headless=False) as browser:
            page = browser.new_page()
            for platform in platforms:
                query = urlencode({"search": f"{platform} {species} {tissue}", "sort": "publishedAt DESC"})
                page.goto(f"{CATALOG}?{query}", wait_until="domcontentloaded", timeout=45_000)
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
                        page.goto(row["source_url"], wait_until="domcontentloaded", timeout=45_000)
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
                    results.append(row)
    return results


class ArchiveBudgetExceeded(ValueError):
    pass


class ArchiveStream(io.RawIOBase):
    """Read a bounded sequential prefix using verified HTTP ranges."""
    def __init__(self, url, budget):
        self.url = url
        self.budget = budget
        self.offset = 0
        self.size = None
        self.pending = b""

    def read(self, length):
        while len(self.pending) < length and (self.size is None or self.offset < self.size):
            end = min(self.offset + 8 * MIB, self.budget, self.size or self.budget) - 1
            if end < self.offset:
                raise ArchiveBudgetExceeded("10x archive exceeded compressed byte budget")
            span = f"{self.offset}-{end}"
            mirror = self.url.replace("https://cf.10xgenomics.com/", "https://s3-us-west-2.amazonaws.com/10x.files/", 1)
            for url in (mirror, self.url):
                try:
                    with urlopen(Request(f"{url}?sragent_range={span}", headers={"Range": f"bytes={span}", "Accept-Encoding": "identity"}),
                                 timeout=25) as response:
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                        if response.status != 206 or not match:
                            raise OSError("10x did not return an exact archive range")
                        start, actual_end, size = map(int, match.groups())
                        if (start != self.offset or actual_end != min(end, size - 1)
                                or (self.size is not None and size != self.size)):
                            raise OSError("10x archive range does not match the request")
                        expected = actual_end - start + 1
                        data = response.read(expected)
                        if len(data) != expected:
                            raise OSError("Truncated archive range")
                    self.size = size
                    self.offset += len(data)
                    self.pending += data
                    break
                except OSError as exc:
                    error = exc
            else:
                raise OSError("Unable to read 10x archive range") from error
        data, self.pending = self.pending[:length], self.pending[length:]
        return data


SPATIAL = {"tissue_positions.parquet": "positions", "scalefactors_json.json": "scalefactors",
           "tissue_lowres_image.png": "image"}
EXPRESSION = {"matrix.mtx.gz": "matrix", "barcodes.tsv.gz": "barcodes"}


def collect(url, out, budget):
    stream = ArchiveStream(url, budget)
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
    return files, stream.offset


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
    try:
        files, read_bytes = collect(binned[0], out, budget)
        measured = validate_hd_bin(files, out, 2)
        return {**result, "status": measured["status"], "files_ready": measured["status"] != "missing_files",
                "bins_um": {"2": measured}, "archive_bytes_read": read_bytes}
    except ArchiveBudgetExceeded as exc:
        return {**result, "status": "download_budget_exceeded", "reason": str(exc)}
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        return {**result, "status": "processing_failed", "error_type": type(exc).__name__, "reason": str(exc)}
