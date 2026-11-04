"""Limited Visium file consistency checks, not biological registration validation."""

import csv
import gzip
import json
import math
import re
import tempfile
import struct
from pathlib import Path


def text_lines(path, *, max_rows=1_000_000):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        count = 0
        while True:
            line = stream.readline(1_000_001)
            if not line:
                break
            count += 1
            if len(line) > 1_000_000 or count > max_rows:
                raise ValueError("Text size limit exceeded; use a dedicated large-scale validator")
            yield line


POSITION_COLUMNS = ["barcode", "in_tissue", "array_row", "array_col",
                    "pxl_row_in_fullres", "pxl_col_in_fullres"]


def parquet_positions(path):
    import pyarrow.parquet as pq

    def batches(source):
        file = pq.ParquetFile(source)
        if file.metadata.num_rows > 32_000_000:
            raise ValueError("Parquet positions row limit exceeded")
        yield POSITION_COLUMNS
        for batch in file.iter_batches(batch_size=65536, columns=POSITION_COLUMNS):
            yield from zip(*(column.to_pylist() for column in batch.columns))

    if not str(path).endswith(".gz"):
        yield from batches(path)
        return
    with gzip.open(path, "rb") as compressed, tempfile.TemporaryFile() as unwrapped:
        total = 0
        while chunk := compressed.read(1024**2):
            total += len(chunk)
            if total > 512 * 1024**2:
                raise ValueError("Uncompressed Parquet positions size limit exceeded")
            unwrapped.write(chunk)
        unwrapped.seek(0)
        yield from batches(unwrapped)


def validate_visium(barcodes: Path, positions: Path, scalefactors=None, image=None, matrix=None, *, bin_um=None,
                    full_matrix=False, overlay=None):
    if full_matrix and matrix is None:
        raise ValueError("Full matrix verification requires a Matrix Market file")
    if overlay is not None and (scalefactors is None or image is None):
        raise ValueError("An overlay requires scale factors and a low-resolution image")
    barcodes_list = [line.strip().split("\t")[0] for line in text_lines(barcodes, max_rows=32_000_000 if bin_um is not None else 1_000_000) if line.strip()]
    if not barcodes_list:
        raise ValueError("No expression barcodes")
    issues = []
    if len(barcodes_list) != len(set(barcodes_list)):
        issues.append("duplicate_expression_barcodes")
    expression_ids = set(barcodes_list)
    bin_pattern = re.compile(rf"s\d*_{bin_um:03d}um_\d+_\d+(?:-\d+)?") if bin_um is not None else None
    if bin_pattern and any(not bin_pattern.fullmatch(barcode) for barcode in expression_ids):
        issues.append("expression_barcode_bin_size_mismatch")
    rows = iter(parquet_positions(positions)) if bin_pattern else csv.reader(text_lines(positions))
    first = next(rows, None)
    if first is None:
        raise ValueError("No tissue positions")
    header = [value.strip() for value in first]
    required = POSITION_COLUMNS
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
    position_count = 0
    wrong_bin_positions = 0
    for row in rows:
        if not row:
            continue
        if len(row) <= max(indexes):
            raise ValueError("Malformed tissue positions row")
        barcode, tissue, ar, ac, y, x = [row[index] for index in indexes]
        position_count += 1
        if bin_pattern:
            if not isinstance(barcode, str) or not bin_pattern.fullmatch(barcode):
                wrong_bin_positions += 1
            if barcode not in expression_ids:
                continue
        try:
            in_tissue = int(tissue)
            y, x = float(y), float(x)
            if in_tissue not in {0, 1} or not all(math.isfinite(v) and v >= 0 for v in (y, x)):
                raise ValueError("Invalid coordinate")
        except (ValueError, TypeError):
            invalid_positions += 1
            continue
        duplicate_positions += barcode in points
        points[barcode] = {"x": x, "y": y, "in_tissue": in_tissue}
    if wrong_bin_positions:
        issues.append("position_barcode_bin_size_mismatch")
    if duplicate_positions:
        issues.append("duplicate_position_barcodes")
    if invalid_positions:
        issues.append("invalid_coordinates_or_tissue_flags")
    missing = sorted(expression_ids - points.keys())
    if missing:
        issues.append("expression_barcodes_missing_from_positions")
    matrix_columns = None
    matrix_content = None
    if matrix:
        lines = text_lines(matrix, max_rows=250_000_010 if full_matrix else 1_000_000)
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
        if full_matrix:
            if magic.lower().split() != ["%%matrixmarket", "matrix", "coordinate", "integer", "general"]:
                raise ValueError("Full verification expects a general integer count matrix")
            if n_genes <= 0 or matrix_columns <= 0 or not 0 <= nonzeros <= 250_000_000:
                raise ValueError("Count matrix dimensions exceed the full verification limits")
            observed = 0
            invalid = 0
            for line in lines:
                if not line.strip() or line.startswith("%"):
                    continue
                observed += 1
                try:
                    gene, column, count = map(int, line.split())
                    invalid += not (1 <= gene <= n_genes and 1 <= column <= matrix_columns and count >= 0)
                except ValueError:
                    invalid += 1
            matrix_content = {"declared_entries": nonzeros, "read_entries": observed, "invalid_entries": invalid,
                              "scope": "complete_matrix_market_stream_indices_and_nonnegative_integer_counts"}
            if observed != nonzeros or invalid:
                issues.append("invalid_matrix_entries_or_entry_count")
    image_check = None
    if scalefactors and image:
        text = "".join(text_lines(scalefactors))
        scales = json.loads(text)
        scale = float(scales["tissue_lowres_scalef"])
        reported_bin_um = scales.get("bin_size_um")
        if bin_pattern and reported_bin_um is not None and float(reported_bin_um) != bin_um:
            issues.append("scalefactors_bin_size_mismatch")
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
        selected = 0
        outside = 0
        stride = max(1, math.ceil(len(expression_ids) / 4000))
        sampled = []
        for key, point in points.items():
            if key not in expression_ids or not point["in_tissue"]:
                continue
            selected += 1
            x, y = point["x"] * scale, point["y"] * scale
            if 0 <= x < width and 0 <= y < height:
                if overlay is not None and (selected - 1) % stride == 0:
                    sampled.append((x, y))
            else:
                outside += 1
        image_check = {"width": width, "height": height, "scale": scale,
                       "tested_in_tissue_barcodes": selected, "out_of_image_count": outside}
        if overlay is not None:
            from PIL import Image, ImageDraw

            with opener(image, "rb") as stream, Image.open(stream) as original:
                preview = original.convert("RGB")
            draw = ImageDraw.Draw(preview, "RGBA")
            for x, y in sampled:
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(0, 190, 255, 150))
            overlay.parent.mkdir(parents=True, exist_ok=True)
            with overlay.open("xb") as stream:
                preview.save(stream, format="PNG")
            image_check["overlay"] = {"filename": overlay.name, "plotted_points": len(sampled),
                                      "sampling_stride": stride, "review": "pending"}
        if bin_pattern:
            image_check["bin_size_um_in_scalefactors"] = reported_bin_um
        if bin_pattern and not selected:
            issues.append("no_expression_barcodes_in_tissue")
        if outside:
            issues.append("scaled_coordinates_outside_lowres_image")
    result = {"expression_barcode_count": len(barcodes_list), "position_count": position_count if bin_pattern else len(points),
            "missing_expression_ids_count": len(missing), "missing_ids_preview": missing[:10],
            "matrix_columns_checked": matrix_columns, "image_bounds_check": image_check,
            "issues": issues, "basic_checks_passed": not issues,
            "coordinate_convention": "position file full-resolution image pixels; physical micrometre scale not inferred",
            "not_verified": ["same physical section or donor", "matrix values and feature identities",
                             "image pixel contents or anatomical registration", "cell segmentation or pathology annotation"],
            "scope": "barcode/position/optional matrix-header and image-bounds consistency only"}
    if matrix_content is not None:
        result["matrix_content"] = matrix_content
        result["not_verified"].remove("matrix values and feature identities")
        result["not_verified"].append("biological validity of counts and feature identities")
        result["scope"] += "; full Matrix Market entry verification"
    return result
