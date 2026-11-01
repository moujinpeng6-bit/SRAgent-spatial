"""Limited Visium file consistency checks, not biological registration validation."""

import csv
import gzip
import json
import math
import struct
from pathlib import Path


def text_lines(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        count = 0
        while True:
            line = stream.readline(1_000_001)
            if not line:
                break
            count += 1
            if len(line) > 1_000_000 or count > 1_000_000:
                raise ValueError("Text size limit exceeded; use a dedicated large-scale validator")
            yield line


def validate_visium(barcodes: Path, positions: Path, scalefactors=None, image=None, matrix=None):
    barcodes_list = [line.strip().split("\t")[0] for line in text_lines(barcodes) if line.strip()]
    if not barcodes_list:
        raise ValueError("No expression barcodes")
    issues = []
    if len(barcodes_list) != len(set(barcodes_list)):
        issues.append("duplicate_expression_barcodes")
    rows = csv.reader(text_lines(positions))
    first = next(rows, None)
    if first is None:
        raise ValueError("No tissue positions")
    header = [value.strip() for value in first]
    required = ["barcode", "in_tissue", "array_row", "array_col", "pxl_row_in_fullres", "pxl_col_in_fullres"]
    if "barcode" in header:
        if not set(required) <= set(header):
            raise ValueError("Unsupported tissue_positions header")
        indexes = [header.index(key) for key in required]
    else:
        import itertools
        rows = itertools.chain([first], rows)
        indexes = list(range(6))
    points = {}
    duplicate_positions = 0
    invalid_positions = 0
    for row in rows:
        if not row:
            continue
        if len(row) <= max(indexes):
            raise ValueError("Malformed tissue positions row")
        barcode, tissue, ar, ac, y, x = [row[index] for index in indexes]
        try:
            in_tissue = int(tissue)
            y, x = float(y), float(x)
            if in_tissue not in {0, 1} or not all(math.isfinite(v) and v >= 0 for v in (y, x)):
                raise ValueError("Invalid coordinate")
        except ValueError:
            invalid_positions += 1
            continue
        duplicate_positions += barcode in points
        points[barcode] = {"x": x, "y": y, "in_tissue": in_tissue}
    if duplicate_positions:
        issues.append("duplicate_position_barcodes")
    if invalid_positions:
        issues.append("invalid_coordinates_or_tissue_flags")
    missing = sorted(set(barcodes_list) - points.keys())
    if missing:
        issues.append("expression_barcodes_missing_from_positions")
    matrix_columns = None
    if matrix:
        lines = text_lines(matrix)
        magic = next(lines, "")
        if not magic.lower().startswith("%%matrixmarket matrix coordinate"):
            raise ValueError("Expected coordinate Matrix Market matrix")
        dimensions = next((line for line in lines if line.strip() and not line.startswith("%")), "")
        parts = dimensions.split()
        if len(parts) != 3:
            raise ValueError("Invalid matrix dimensions")
        n_genes, matrix_columns, nonzeros = map(int, parts)
        if min(n_genes, matrix_columns, nonzeros) < 0 or matrix_columns != len(barcodes_list):
            issues.append("matrix_column_count_does_not_match_barcodes")
    image_check = None
    if scalefactors and image:
        text = "".join(text_lines(scalefactors))
        scale = float(json.loads(text)["tissue_lowres_scalef"])
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("Invalid low-resolution scale factor")
        opener = gzip.open if str(image).endswith(".gz") else open
        with opener(image, "rb") as stream:
            prefix = stream.read(24)
        if len(prefix) < 24 or prefix[:8] != b"\x89PNG\r\n\x1a\n" or prefix[12:16] != b"IHDR":
            raise ValueError("Only a PNG image header is supported")
        width, height = struct.unpack(">II", prefix[16:24])
        if not width or not height:
            raise ValueError("Invalid PNG dimensions")
        selected = [points[key] for key in set(barcodes_list) & points.keys() if points[key]["in_tissue"]]
        outside = sum(not (0 <= p["x"] * scale < width and 0 <= p["y"] * scale < height) for p in selected)
        image_check = {"width": width, "height": height, "scale": scale,
                       "tested_in_tissue_barcodes": len(selected), "out_of_image_count": outside}
        if outside:
            issues.append("scaled_coordinates_outside_lowres_image")
    return {"expression_barcode_count": len(barcodes_list), "position_count": len(points),
            "missing_expression_ids_count": len(missing), "missing_ids_preview": missing[:10],
            "matrix_columns_checked": matrix_columns, "image_bounds_check": image_check,
            "issues": issues, "basic_checks_passed": not issues,
            "coordinate_convention": "position file full-resolution image pixels; physical micrometre scale not inferred",
            "not_verified": ["same physical section or donor", "matrix values and feature identities",
                             "image pixel contents or anatomical registration", "cell segmentation or pathology annotation"],
            "scope": "barcode/position/optional matrix-header and image-bounds consistency only"}
