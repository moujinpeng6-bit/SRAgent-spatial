"""Prepare sample-owned Visium HD tar archives for bin-level verification."""
import gzip
import json
import re
import tempfile
import tarfile
from pathlib import Path, PurePosixPath

from .asset_audit import decode_image, matrix_dimensions
from .geo import write_json
from .spatial import validate_visium
from .xenium_assets import AmbiguousBundle, archive_plan, download_archive, find_archive


ROLE_NAMES = {
    "barcodes": r"(?:^|_)barcodes\.tsv(?:\.gz)?$",
    "matrix": r"(?:^|_)matrix\.mtx(?:\.gz)?$",
    "h5": r"(?:^|_)filtered_feature_bc_matrix\.h5(?:\.gz)?$",
    "positions": r"(?:^|_)tissue_positions\.parquet(?:\.gz)?$",
    "scalefactors": r"(?:^|_)scalefactors_json\.json(?:\.gz)?$",
    "image": r"(?:^|_)tissue_lowres_image\.png(?:\.gz)?$",
}


def prepare_hd_archive(archive, out):
    """Select only validation assets and preserve archive member provenance."""
    groups = {}
    scanned = 0
    prepared = 0
    with tarfile.open(archive, "r|*") as stream:
        for index, member in enumerate(stream, 1):
            scanned += member.size
            if index > 100_000 or scanned > 24 * 1024**3:
                raise ValueError("HD archive scan limit exceeded")
            path = PurePosixPath(member.name)
            role = next((key for key, pattern in ROLE_NAMES.items() if re.search(pattern, path.name, re.I)), None)
            if role is None:
                continue
            if (not member.isfile() or path.is_absolute() or ".." in path.parts or "\\" in member.name):
                raise ValueError("Unsafe HD archive member")
            sizes = {int(value) for value in re.findall(r"(?:^|[/_])(0?0?[28]|0?16)um(?=[/_]|$)", str(path), re.I)}
            if len(sizes) > 1:
                raise ValueError("Conflicting bin resolutions in archive member")
            size = next(iter(sizes), None)
            # A bin belongs to a specific square directory, or to the flat sample directory.
            anchors = [i for i, part in enumerate(path.parts[:-1]) if re.fullmatch(r"square_(002|008|016)um", part)]
            anchor = PurePosixPath(*path.parts[:anchors[-1]+1]) if anchors else path.parent
            key = (size, str(anchor))
            bucket = groups.setdefault(key, {})
            if role in bucket:
                raise AmbiguousBundle("Ambiguous HD archive role within bin")
            limit = 65536 if role == "matrix" else 512 * 1024**2
            if role != "matrix" and member.size > limit:
                raise ValueError("HD archive member exceeds preparation limit")
            prepared += min(member.size, limit)
            if prepared > 3 * 1024**3:
                raise ValueError("HD archive preparation byte limit exceeded")
            dest = out / "prepared" / str(index) / path.name
            dest.parent.mkdir(parents=True)
            with stream.extractfile(member) as source, dest.open("xb") as target:
                remaining = min(member.size, limit)
                while remaining:
                    chunk = source.read(min(remaining, 1024**2))
                    if not chunk:
                        raise ValueError("Truncated HD archive member")
                    target.write(chunk)
                    remaining -= len(chunk)
            bucket[role] = {"path": dest, "member": member.name}
    roots = {str(PurePosixPath(anchor).parent) if PurePosixPath(anchor).name.startswith("square_")
             else anchor for size, anchor in groups if size is not None}
    if len(roots) > 1:
        raise AmbiguousBundle("Multiple sample roots in HD archive")
    bins = {}
    for (size, anchor), files in groups.items():
        if size is None:
            continue
        if size in bins:
            raise AmbiguousBundle("Multiple archive directories for the same HD bin")
        shared = groups.get((None, anchor), {})
        # An untagged file can be associated only with the sole bin in that same directory.
        if sum(key[0] is not None and key[1] == anchor for key in groups) == 1:
            files = {**shared, **files}
        bins[size] = files
    return bins


def spatial_bin_size(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("HD scale factors exceed size limit")
    size = json.loads(raw)["bin_size_um"]
    if size not in (2, 8, 16, 24):
        raise ValueError("Unsupported declared HD resolution")
    return int(size)


def hdf5_barcodes(path, target):
    import h5py

    with tempfile.TemporaryFile() as unwrapped:
        source = path
        if str(path).endswith(".gz"):
            with gzip.open(path, "rb") as compressed:
                total = 0
                while chunk := compressed.read(1024**2):
                    total += len(chunk)
                    if total > 512 * 1024**2:
                        raise ValueError("Uncompressed HDF5 exceeds validation limit")
                    unwrapped.write(chunk)
            unwrapped.seek(0)
            source = unwrapped
        with h5py.File(source, "r") as h5, target.open("x", encoding="utf-8") as output:
            matrix = h5["matrix"]
            features, observations = map(int, matrix["shape"][:])
            ids = matrix["barcodes"]
            if ids.shape != (observations,) or not 0 < observations <= 32_000_000 or features <= 0:
                raise ValueError("Invalid HD HDF5 barcode dimensions")
            for start in range(0, observations, 65536):
                output.writelines(value.decode("utf-8") + "\n" for value in ids[start:start+65536])
    return {"features": features, "observations": observations, "scope": "hdf5_shape_and_barcodes"}


def validate_hd_bin(files, out, size):
    required = {"positions", "scalefactors", "image"}
    missing = sorted(required - files.keys())
    if not ("h5" in files or {"barcodes", "matrix"} <= files.keys()):
        missing.append("expression_bundle")
    result = {"archive_members": {role: info["member"] for role, info in files.items()}}
    if missing:
        return {**result, "status": "missing_files", "missing_roles": missing}
    paths = {role: info["path"] for role, info in files.items()}
    if "h5" in paths:
        out.mkdir(parents=True, exist_ok=True)
        barcodes = out / "expression_barcodes.tsv"
        dims = hdf5_barcodes(paths["h5"], barcodes)
    else:
        barcodes = paths["barcodes"]
        with paths["matrix"].open("rb") as stream:
            prefix = stream.read(65536)
            dims = matrix_dimensions(prefix, prefix.startswith(b"\x1f\x8b"))
    basic = validate_visium(barcodes, paths["positions"], paths["scalefactors"], paths["image"], bin_um=size)
    image = decode_image(paths["image"])
    columns_match = dims["observations"] == basic["expression_barcode_count"]
    result["checks"] = {"barcode_position_image": basic, "matrix_dimensions": dims,
                        "matrix_columns_match_barcodes": columns_match, "image_decode": image}
    result["status"] = "correspondence_passed" if columns_match and not basic["issues"] else "mismatch"
    return result


def audit_hd_geo_archive(row, root, out, download_bytes):
    plan = archive_plan(row["accession"], {"Sample_supplementary_file": [item["url"] for item in row["assets"]]})
    archive = find_archive(root, plan["filename"])
    mode = "exact_local_archive_reuse"
    if archive is None:
        if not download_bytes:
            raise FileNotFoundError("No complete local HD archive")
        archive = download_archive(plan, out / "downloads", download_bytes)
        mode = "downloaded_from_sample_url"
    return audit_prepared_archive(archive, out, {"source_url": plan["source_url"], "filename": plan["filename"],
                                                "bytes": archive.stat().st_size, "mode": mode})


def audit_prepared_archive(archive, out, provenance):
    before = archive.stat()
    groups = prepare_hd_archive(archive, out)
    after = archive.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("HD archive changed during preparation")
    bins = {}
    for size, files in sorted(groups.items()):
        target = out / f"square_{size:03d}um"
        try:
            result = validate_hd_bin(files, target, size)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            result = {"status": "validation_error", "error_type": type(exc).__name__}
        bins[str(size)] = result
        write_json(target / "validation.json", result)
    status = "missing_files" if not bins else "completed_with_issues"
    if bins and all(item["status"] == "correspondence_passed" for item in bins.values()):
        status = "correspondence_passed"
    return {"stage": "validate", "status": status, "bins_um": bins,
            "unlisted_standard_bin_sizes_um": sorted({2, 8, 16} - groups.keys()),
            "archive": provenance}
