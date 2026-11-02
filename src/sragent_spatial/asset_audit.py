"""Sample-scoped Visium file audit. Network failure is never a correspondence pass."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from .geo import write_json
from .spatial import text_lines, validate_visium

PATTERNS = {
    "barcodes": r"_barcodes\.tsv(?:\.gz)?$",
    "features": r"_features\.tsv(?:\.gz)?$",
    "matrix": r"_matrix\.mtx(?:\.gz)?$",
    "positions": r"_tissue_positions(?:_list)?\.csv(?:\.gz)?$",
    "scalefactors": r"_scalefactors_json\.json(?:\.gz)?$",
    "image": r"_tissue_lowres_image\.png(?:\.gz)?$",
    "h5": r"_filtered_feature_bc_matrix\.h5$",
}
REQUIRED = ("barcodes", "matrix", "positions", "scalefactors", "image")


def file_plan(sample):
    acc = sample["accession"]
    if not re.fullmatch(r"GSM[1-9][0-9]*", acc):
        raise ValueError("Expected a GSM sample")
    by_role = {role: [] for role in PATTERNS}
    rejected = []
    for asset in sample.get("assets", []):
        parts = urlsplit(asset["url"])
        name = unquote(parts.path.rsplit("/", 1)[-1])
        if parts.hostname != "ftp.ncbi.nlm.nih.gov" or f"/{acc}/suppl/" not in parts.path or not name.startswith(acc + "_") or any(c in name for c in '/\\<>:"|?*\r\n'):
            rejected.append(asset["url"])
            continue
        for role, pattern in PATTERNS.items():
            match = re.search(pattern, name, re.I)
            if match:
                core = name[len(acc) + 1:match.start()]
                anchor = "_".join(x for x in core.lower().split("_") if x and x != "filtered")
                item = {"role": role, "filename": name, "bundle_anchor": anchor,
                        "source_url": asset["url"],
                        "download_url": urlunsplit(("https", "ftp.ncbi.nlm.nih.gov", parts.path, "", ""))}
                if item not in by_role[role]:
                    by_role[role].append(item)
    ambiguous = [role for role, values in by_role.items() if len(values) > 1]
    chosen = {role: values[0] for role, values in by_role.items() if len(values) == 1}
    anchors = {value["bundle_anchor"] for value in chosen.values() if value["bundle_anchor"]}
    return {"roles": chosen, "missing_roles": [role for role in REQUIRED if role not in chosen],
            "ambiguous_roles": ambiguous, "bundle_anchors": sorted(anchors),
            "metadata_bundle_consistent": not ambiguous and len(anchors) <= 1 and not rejected,
            "rejected_urls": rejected}


class FileFetcher:
    def __init__(self, root, *, interval=0.35):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.interval = interval

    def fetch(self, accession, asset):
        role = asset["role"]
        partial = role == "matrix"
        limit = 65536 if partial else (32 * 1024 * 1024 if role == "h5" else 8 * 1024 * 1024)
        directory = self.root / accession
        filename = asset["filename"] + (".prefix" if partial else "")
        path = directory / filename
        meta_path = directory / (filename + ".download.json")
        result = {**asset, "status": "not_read", "download_complete": False}
        if path.is_file() and meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["download_url"] == asset["download_url"] and hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"]:
                return {**meta, "cached": True, "local_path": str(path)}
        headers = {"User-Agent": "SRAgent-spatial/0.4 (public Visium correspondence audit)", "Accept-Encoding": "identity"}
        if partial:
            headers["Range"] = "bytes=0-65535"
        try:
            with self.lock:
                time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
            import truststore
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with urllib.request.urlopen(urllib.request.Request(asset["download_url"], headers=headers), timeout=30, context=context) as response:
                length = response.headers.get("Content-Length")
                if not partial and length and int(length) > limit:
                    return {**result, "status": "size_limit", "http_status": response.status, "content_length": int(length)}
                body = response.read(limit if partial else limit + 1)
                if not body or len(body) > limit:
                    return {**result, "status": "size_limit_or_empty", "http_status": response.status}
                result.update(status="downloaded", http_status=response.status, bytes=len(body),
                    content_range=response.headers.get("Content-Range"), content_length=length,
                    download_complete=not partial, sha256=hashlib.sha256(body).hexdigest(),
                    checksum_scope="downloaded_compressed_prefix_only" if partial else "complete_downloaded_file")
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            write_json(meta_path, result)
            return {**result, "cached": False, "local_path": str(path)}
        except urllib.error.HTTPError as exc:
            return {**result, "status": "unavailable", "http_status": exc.code}
        except Exception as exc:
            return {**result, "status": "read_error", "error_type": type(exc).__name__}


class LocalFileFetcher:
    """Read only explicitly needed original GEO filenames under a user-specified root."""
    def __init__(self, root, wanted):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError("Local data directory is unavailable")
        self.matches = {name: [] for name in wanted}
        count = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
            for name in files:
                count += 1
                if count > 50000:
                    raise ValueError("Local scan exceeded 50000 files; specify a narrower directory")
                if name in self.matches:
                    path = (Path(directory) / name).resolve()
                    if path.is_relative_to(root) and not (Path(directory) / name).is_symlink():
                        self.matches[name].append(path)

    def fetch(self, accession, asset):
        paths = self.matches.get(asset["filename"], [])
        result = {**asset, "transport": "user_provided_local_file", "download_complete": False}
        if len(paths) != 1:
            return {**result, "status": "missing_local_file" if not paths else "ambiguous_local_file"}
        path = paths[0]
        partial = asset["role"] == "matrix"
        limit = 65536 if partial else (32 * 1024 * 1024 if asset["role"] == "h5" else 8 * 1024 * 1024)
        if not partial and path.stat().st_size > limit:
            return {**result, "status": "size_limit"}
        with path.open("rb") as stream:
            body = stream.read(limit)
        # Validators read the original local file for small companions; matrix parsing
        # below reads at most the bounded prefix even for a user-provided full matrix.
        return {**result, "status": "downloaded", "local_path": str(path), "bytes": len(body),
                "download_complete": not partial, "sha256": hashlib.sha256(body).hexdigest(),
                "checksum_scope": "local_compressed_prefix_only" if partial else "complete_local_file"}


def matrix_dimensions(prefix, compressed=True):
    if compressed:
        data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(prefix, 1024 * 1024)
    else:
        data = prefix
    lines = data.decode("utf-8-sig").splitlines()
    if not lines or not lines[0].lower().startswith("%%matrixmarket matrix coordinate"):
        raise ValueError("Expected Matrix Market coordinate header")
    line = next((x for x in lines[1:] if x.strip() and not x.startswith("%")), "")
    fields = line.split()
    if len(fields) != 3 or any(not x.isdigit() for x in fields):
        raise ValueError("Missing or malformed Matrix Market dimensions")
    genes, cells, nnz = map(int, fields)
    if genes <= 0 or cells <= 0:
        raise ValueError("Empty expression matrix")
    return {"features": genes, "observations": cells, "nonzeros": nnz,
            "scope": "header_only_not_matrix_values_or_complete_gzip_integrity"}


def decode_image(path):
    from PIL import Image
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as stream:
        body = stream.read(16 * 1024 * 1024 + 1)
    if len(body) > 16 * 1024 * 1024:
        raise ValueError("Decoded image file exceeds limit")
    with Image.open(io.BytesIO(body)) as image:
        if image.width * image.height > 10000000:
            raise ValueError("Low-resolution image pixel limit exceeded")
        image.verify()
    with Image.open(io.BytesIO(body)) as image:
        image.load()
        return {"width": image.width, "height": image.height, "format": image.format,
                "pixel_decode_verified": True}


def audit_sample(sample, fetcher):
    plan = file_plan(sample)
    row = {"accession": sample["accession"], "studies": sample["studies"], "source_url": sample["source_url"],
           "plan": plan, "files": {}, "checks": {}, "issues": [], "correspondence_verified": False,
           "status": "not_checked", "not_verified": ["physical section or donor identity",
               "anatomical image registration", "matrix values", "cell segmentation or pathology labels"]}
    if not plan["metadata_bundle_consistent"] or plan["ambiguous_roles"]:
        row.update(status="ambiguous_bundle")
        return row
    for role, asset in plan["roles"].items():
        row["files"][role] = fetcher.fetch(sample["accession"], asset)
    unavailable = [role for role in REQUIRED if row["files"].get(role, {}).get("status") != "downloaded"]
    row["unavailable_required_roles"] = unavailable
    # A 403/timeout/missing file is unavailable evidence, not a mismatch or a pass.
    if unavailable:
        row.update(status="unavailable" if not plan["missing_roles"] else "missing_files")
        return row
    files = {role: Path(info["local_path"]) for role, info in row["files"].items() if info["status"] == "downloaded"}
    try:
        basic = validate_visium(files["barcodes"], files["positions"], files["scalefactors"], files["image"])
        with files["matrix"].open("rb") as stream:
            prefix = stream.read(65536)
        dims = matrix_dimensions(prefix, plan["roles"]["matrix"]["filename"].endswith(".gz"))
        row["checks"] = {"barcode_position_image": basic, "matrix_header": dims,
                         "matrix_columns_match_barcodes": dims["observations"] == basic["expression_barcode_count"],
                         "image_decode": decode_image(files["image"])}
        row["issues"].extend(basic["issues"])
        if not row["checks"]["matrix_columns_match_barcodes"]:
            row["issues"].append("matrix_column_count_does_not_match_barcodes")
        if "features" in files:
            features = [line.split("\t")[0] for line in text_lines(files["features"]) if line.strip()]
            row["checks"]["feature_count"] = len(features)
            if len(features) != dims["features"]:
                row["issues"].append("matrix_row_count_does_not_match_features")
        if "h5" in files:
            import h5py
            with h5py.File(files["h5"], "r") as h5:
                barcodes = [x.decode() if isinstance(x, bytes) else str(x) for x in h5["matrix/barcodes"][:]]
                external = [line.strip().split("\t")[0] for line in text_lines(files["barcodes"]) if line.strip()]
                shape = list(map(int, h5["matrix/shape"][:]))
            row["checks"]["h5"] = {"barcodes_identical_to_tsv_in_order": barcodes == external,
                                      "shape": shape, "shape_matches_mtx": shape == [dims["features"], dims["observations"]]}
            if barcodes != external or shape != [dims["features"], dims["observations"]]:
                row["issues"].append("h5_matrix_or_barcodes_disagree_with_mtx_bundle")
        row.update(status="basic_correspondence_passed" if not row["issues"] else "mismatch",
                   correspondence_verified=not row["issues"])
    except Exception as exc:
        row.update(status="validation_error", error_type=type(exc).__name__)
    return row


def audit_run(run_path, out, *, workers=3, fetcher=None, local_root=None):
    run = json.loads(Path(run_path).read_text(encoding="utf-8"))
    samples = [sample for sample in run["samples"] if sample.get("include_in_catalog") and sample["status"] == "completed"]
    if not 1 <= workers <= 3:
        raise ValueError("workers must be 1..3")
    out = Path(out)
    if local_root is not None:
        wanted = {asset["filename"] for sample in samples for asset in file_plan(sample)["roles"].values()}
        fetcher = LocalFileFetcher(local_root, wanted)
    fetcher = fetcher or FileFetcher(out / "files")
    results = []

    def persist():
        ordered = sorted(results, key=lambda row: row["accession"])
        report = {"input_run_sha256": hashlib.sha256(Path(run_path).read_bytes()).hexdigest(),
                  "expected_samples": len(samples), "audited_samples": len(ordered),
                  "status_counts": dict(Counter(row["status"] for row in ordered)), "samples": ordered,
                  "scope": "Listed sample bundle + downloaded file structure and geometry; no physical registration claim"}
        write_json(out / "audit.json", report)
        with (out / "audit.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            fields = ["accession", "studies", "status", "metadata_bundle_consistent", "correspondence_verified",
                      "unavailable_required_roles", "http_statuses", "issues", "source_url"]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in ordered:
                writer.writerow({"accession": row["accession"], "studies": ";".join(row["studies"]),
                    "status": row["status"], "metadata_bundle_consistent": row["plan"]["metadata_bundle_consistent"],
                    "correspondence_verified": row["correspondence_verified"],
                    "unavailable_required_roles": ";".join(row.get("unavailable_required_roles", [])),
                    "http_statuses": json.dumps({role: info.get("http_status") for role, info in row["files"].items()}),
                    "issues": ";".join(row["issues"]), "source_url": row["source_url"]})
        return report

    persist()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit_sample, sample, fetcher): sample["accession"] for sample in samples}
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            persist()
            print(json.dumps({"accession": row["accession"], "asset_status": row["status"], "done": len(results), "total": len(samples)}), flush=True)
    return persist()
