"""Aggregate a verified GEO Visium HD 8 µm count bundle into local 16 µm bins."""
import argparse
import gzip
import json
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.io import mmread
from scipy.sparse import csr_matrix

from sragent_spatial.asset_audit import LocalFileFetcher, audit_sample, file_plan
from sragent_spatial.geo import write_json
from sragent_spatial.spatial import parquet_positions, text_lines
from sragent_spatial.xenium_validation import sha256_file


def derive(row, source, out):
    sample = row["accession"]
    plan = file_plan(row, bin_um=8)
    reader = LocalFileFetcher(source, {asset["filename"] for asset in plan["roles"].values()})
    audit = audit_sample(row, reader, bin_um=8)
    if not audit["correspondence_verified"]:
        raise ValueError(f"8 µm inputs failed correspondence checks for {sample}")
    files = {role: Path(info["local_path"]) for role, info in audit["files"].items()
             if info["status"] == "downloaded"}
    barcodes = [line.strip() for line in text_lines(files["barcodes"])]
    pattern = re.compile(r"s\d*_008um_(\d+)_(\d+)(?:-\d+)?")
    positions = parquet_positions(files["positions"])
    next(positions)
    points = {}
    for barcode, tissue, r, c, y, x in positions:
        match = pattern.fullmatch(barcode)
        if not match or tuple(map(int, match.groups())) != (r, c) or not np.isfinite([y, x]).all():
            raise ValueError("Position barcode, array coordinates or image coordinates disagree")
        group = (r // 2, c // 2)
        sums = points.setdefault(group, [0, 0.0, 0.0, 0])
        sums[0] += 1
        sums[1] += y
        sums[2] += x
        sums[3] = max(sums[3], tissue)
    groups = []
    for barcode in barcodes:
        match = pattern.fullmatch(barcode)
        if match is None:
            raise ValueError("Unexpected 8 µm barcode")
        groups.append(tuple(int(value)//2 for value in match.groups()))
    used = sorted(set(groups))
    index = {group: column for column, group in enumerate(used)}
    with gzip.open(files["matrix"], "rb") as stream:
        matrix = mmread(stream).tocsr()
    if matrix.shape[1] != len(barcodes) or matrix.dtype.kind not in "iu" or np.any(matrix.data < 0):
        raise ValueError("Expected nonnegative integer counts with one column per barcode")
    memberships = csr_matrix((np.ones(len(groups), dtype=np.int64),
                              (np.arange(len(groups)), [index[group] for group in groups])),
                             shape=(len(groups), len(used)))
    aggregated = (matrix.astype(np.int64, copy=False) @ memberships).tocsc()
    original_totals = np.asarray(matrix.sum(axis=1, dtype=np.int64)).ravel()
    if not np.array_equal(original_totals, np.asarray(aggregated.sum(axis=1)).ravel()):
        raise ValueError("Aggregation did not conserve per-gene counts")
    identifiers = [f"s_016um_{r:05d}_{c:05d}-1" for r, c in used]
    with tempfile.TemporaryDirectory(dir=out) as temporary:
        root = Path(temporary) / "square_016um"
        spatial = root / "spatial"
        spatial.mkdir(parents=True)
        with h5py.File(root / "filtered_feature_bc_matrix.h5", "w") as h5:
            m = h5.create_group("matrix")
            m["shape"] = aggregated.shape
            m["barcodes"] = np.asarray(identifiers, dtype="S")
            features = [line.rstrip("\r\n").split("\t") for line in text_lines(files["features"])]
            if len(features) != aggregated.shape[0]:
                raise ValueError("Feature rows do not match matrix")
            group = m.create_group("features")
            group["id"] = np.asarray([item[0].encode() for item in features])
            group["name"] = np.asarray([item[1].encode() for item in features])
            group["feature_type"] = np.asarray([item[2].encode() if len(item)>2 else b"Gene Expression" for item in features])
            for key in ("data", "indices", "indptr"):
                m.create_dataset(key, data=getattr(aggregated, key), compression="gzip")
        pq.write_table(pa.table({
            "barcode": identifiers, "array_row": [r for r, c in used], "array_col": [c for r, c in used],
            "in_tissue": [points[group][3] for group in used],
            "pxl_row_in_fullres": [points[group][1]/points[group][0] for group in used],
            "pxl_col_in_fullres": [points[group][2]/points[group][0] for group in used],
        }), spatial / "tissue_positions.parquet")
        with gzip.open(files["scalefactors"], "rt") as stream:
            scales = json.load(stream)
        if scales["bin_size_um"] != 8:
            raise ValueError("Expected 8 µm scale metadata")
        scales["bin_size_um"] = 16
        scales["spot_diameter_fullres"] *= 2
        write_json(spatial / "scalefactors_json.json", scales)
        with gzip.open(files["image"], "rb") as source_image, (spatial / "tissue_lowres_image.png").open("wb") as image:
            shutil.copyfileobj(source_image, image)
        archive = out / f"{sample}_derived_016um.tar.gz"
        with tarfile.open(archive, "x:gz", compresslevel=1) as stream:
            stream.add(root, arcname=root.name)
    provenance = {"filename": archive.name, "sha256": sha256_file(archive),
                  "method": "2x2 8um array grouping; coordinates are means of available constituent positions",
                  "source_bin_um": 8, "derived_bin_um": 16, "per_gene_counts_conserved": True,
                  "total_counts": int(original_totals.sum()),
                  "source_files": {role: {"sha256": sha256_file(path), "url": plan["roles"][role]["source_url"]}
                                   for role, path in files.items()}}
    return {**row, "local_hd_derivative": provenance}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("run", "source", "out"):
        parser.add_argument(f"--{arg}", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.run.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=False)
    samples = []
    for row in report["samples"]:
        if row.get("status") == "completed" and row.get("include_in_catalog") is True:
            samples.append(derive(row, args.source, args.out))
            print(row["accession"], "derived 16 µm", flush=True)
    write_json(args.out / "candidates.json", {**report, "samples": samples})


if __name__ == "__main__":
    main()
