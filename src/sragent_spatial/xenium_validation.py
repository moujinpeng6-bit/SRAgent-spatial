"""Bounded, read-only Xenium expression/coordinate/image correspondence checks.

No LLM or repository-specific sample paths. Input paths are supplied by a local
manifest, not by model-generated tool arguments. No raw data are uploaded here.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

MAX_SMALL_BYTES = 64 * 1024 * 1024
MAX_MATRIX_BYTES = 256 * 1024 * 1024
MAX_CELLS = 200_000
MAX_FEATURES = 20_000
MAX_NNZ = 20_000_000
MAX_PREVIEW_ELEMENTS = 12_000_000


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return sha256_stream(stream)


def sha256_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(4 * 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def read_csv(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rb") as stream:
        body = stream.read(MAX_SMALL_BYTES + 1)
    if len(body) > MAX_SMALL_BYTES:
        raise ValueError("CSV exceeds the decompressed read limit")
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def read_preview(path):
    """Select a bounded pre-existing pyramid level before allocating pixels."""
    import tifffile

    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        if series.axes not in {"YX", "CYX"}:
            raise ValueError("Expected a YX or CYX morphology focus image")
        candidates = [(i, level) for i, level in enumerate(series.levels)
                      if math.prod(level.shape) <= MAX_PREVIEW_ELEMENTS]
        if not candidates:
            raise ValueError("No image pyramid level fits the preview budget")
        index, level = candidates[0]
        pixels = ET.fromstring(tif.ome_metadata).find(".//{*}Pixels").attrib
        # OME schema defaults PhysicalSize units to micrometers when omitted.
        for key in ("PhysicalSizeXUnit", "PhysicalSizeYUnit"):
            if pixels.get(key, "µm") not in {"µm", "um", "μm"}:
                raise ValueError("Unsupported image physical unit; expected micrometers")
        info = {"full_shape": list(series.shape), "axes": series.axes,
                "preview_shape": list(level.shape), "pyramid_level": index,
                "pixel_size_x_um": float(pixels["PhysicalSizeX"]),
                "pixel_size_y_um": float(pixels["PhysicalSizeY"]),
                "physical_unit_defaulted": "PhysicalSizeXUnit" not in pixels or "PhysicalSizeYUnit" not in pixels}
        preview = level.asarray()
        if series.axes == "CYX":
            preview = preview[0]
            info["preview_channel"] = 0
    return preview, info


def verify_archive(archive_path, prefix, files):
    """Hash exact bundle members, with no extraction and explicit scan limits."""
    before = Path(archive_path).stat()
    prefix = prefix.strip("/")
    if not prefix or ".." in Path(prefix).parts or "\\" in prefix:
        raise ValueError("Invalid archive prefix")
    expected = {prefix + "/" + name: (name, sha256_file(path)) for name, path in files.items()}
    seen, hashes, scanned = set(), {}, 0
    with tarfile.open(archive_path, "r|gz") as archive:
        for count, member in enumerate(archive, 1):
            scanned += member.size
            if scanned > 16 * 1024**3 or count > 100_000:
                raise ValueError("Archive scan budget exceeded")
            if member.name not in expected:
                continue
            if not member.isfile() or member.name in seen:
                raise ValueError("Ambiguous or non-regular archive member")
            seen.add(member.name)
            name, local_hash = expected[member.name]
            with archive.extractfile(member) as stream:
                member_hash = sha256_stream(stream)
            hashes[name] = {"local_sha256": local_hash, "archive_sha256": member_hash,
                            "matches": local_hash == member_hash}
    after = Path(archive_path).stat()
    unchanged = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    return {"all_members_found": seen == set(expected), "source_unchanged": unchanged,
            "all_hashes_match": bool(hashes) and all(row["matches"] for row in hashes.values()),
            "members": hashes, "missing_members": sorted(set(expected) - seen)}


def validate_xenium(spec, out):
    """Validate one registered Xenium sample, then persist a machine-owned report."""
    import h5py
    import numpy as np
    from scipy.sparse import csc_matrix
    from scipy.spatial import cKDTree

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data_dir, image_path = Path(spec["data_dir"]), Path(spec["image"])
    if spec.get("platform") != "Xenium" or spec.get("coordinate_unit") != "um":
        raise ValueError("This validator requires explicit Xenium / um inputs")
    files = {name: data_dir / name for name in ["experiment.xenium", "cells.csv.gz", "cell_feature_matrix.h5"]}
    for name, path in files.items():
        limit = MAX_MATRIX_BYTES if name.endswith(".h5") else MAX_SMALL_BYTES
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("Required input missing or exceeds read limit: " + name)
    experiment = json.loads(files["experiment.xenium"].read_text(encoding="utf-8-sig"))
    cells = read_csv(files["cells.csv.gz"])
    if not 2 <= len(cells) <= MAX_CELLS:
        raise ValueError("Cell count is outside the supported 2..200000 range")
    ids = np.array([row["cell_id"] for row in cells])
    xy = np.array([[float(row["x_centroid"]), float(row["y_centroid"])] for row in cells])
    if not np.isfinite(xy).all():
        raise ValueError("Non-finite cell coordinates")
    with h5py.File(files["cell_feature_matrix.h5"], "r") as f:
        m = f["matrix"]
        shape = tuple(int(x) for x in m["shape"][:])
        if len(shape) != 2 or not 1 <= shape[0] <= MAX_FEATURES or not 2 <= shape[1] <= MAX_CELLS:
            raise ValueError("Matrix dimensions exceed supported limits")
        if m["data"].size > MAX_NNZ or m["indices"].size != m["data"].size or m["indptr"].size != shape[1] + 1:
            raise ValueError("Matrix sparse arrays invalid or exceed read limits")
        barcodes = m["barcodes"].asstr()[:]
        names = m["features/name"].asstr()[:]
        types = m["features/feature_type"].asstr()[:]
        matrix = csc_matrix((m["data"][:], m["indices"][:], m["indptr"][:]), shape=shape)
    matrix.check_format(full_check=True)
    if len(names) != shape[0] or len(types) != shape[0] or len(barcodes) != shape[1]:
        raise ValueError("Feature/barcode dimensions do not match matrix")
    checks = {"unique_cell_ids": len(set(ids)) == len(ids),
              "unique_matrix_barcodes": len(set(barcodes)) == len(barcodes),
              "cell_id_set_matches_matrix": set(ids) == set(barcodes),
              "finite_coordinates": True,
              "experiment_cell_count_matches": experiment.get("num_cells") == len(cells),
              "nonnegative_integer_expression": bool(np.isfinite(matrix.data).all() and (matrix.data >= 0).all()
                                                      and (matrix.data == np.floor(matrix.data)).all())}
    original_order = bool(np.array_equal(ids, barcodes))
    # Reconcile by identity, never assume equal-length tables have matching order.
    if not all(checks[key] for key in ["unique_cell_ids", "unique_matrix_barcodes", "cell_id_set_matches_matrix"]):
        raise ValueError("Nonunique or mismatched cell identities")
    index = {cell_id: i for i, cell_id in enumerate(barcodes)}
    matrix = matrix[:, [index[cell_id] for cell_id in ids]]
    checks["matrix_order_reconciled_by_id"] = True
    gene_mask = types == "Gene Expression"
    checks["gene_features_present"] = bool(gene_mask.any())
    gene_sums = np.asarray(matrix[gene_mask].sum(axis=0)).ravel()
    all_sums = np.asarray(matrix.sum(axis=0)).ravel()
    checks["gene_counts_equal_cells_summary"] = bool(np.array_equal(gene_sums, [int(row["transcript_counts"]) for row in cells]))
    checks["all_feature_counts_equal_cells_summary"] = bool(np.array_equal(all_sums, [int(row["total_counts"]) for row in cells]))
    preview, image = read_preview(image_path)
    height, width = image["full_shape"][-2:]
    pixel_size = float(experiment["pixel_size"])
    if not math.isfinite(pixel_size) or pixel_size <= 0:
        raise ValueError("Invalid experiment pixel size")
    checks["pixel_size_agrees_with_ome"] = bool(np.isclose(pixel_size, image["pixel_size_x_um"])
                                                 and np.isclose(pixel_size, image["pixel_size_y_um"]))
    image_extent = np.array([width * pixel_size, height * pixel_size])
    inside = (xy >= 0).all(axis=1) & (xy < image_extent).all(axis=1)
    checks["all_centroids_inside_image"] = bool(inside.all())
    optional_missing = []
    roi_boundaries = []
    tree = cKDTree(xy)
    anchor = int(tree.query(np.median(xy, axis=0))[1])
    roi_mask = np.linalg.norm(xy - xy[anchor], axis=1) <= 150
    roi_ids = set(ids[roi_mask])
    for name, label in [("cell_boundaries.csv.gz", "cell"), ("nucleus_boundaries.csv.gz", "nucleus")]:
        path = data_dir / name
        if not path.exists():
            optional_missing.append(name)
            continue
        files[name] = path
        rows = read_csv(path)
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["cell_id"]].append((float(row["vertex_x"]), float(row["vertex_y"])))
        vertices = np.array([point for points in grouped.values() for point in points])
        checks[label + "_boundary_ids_match"] = set(grouped) == set(ids)
        valid = vertices.ndim == 2 and vertices.shape[1] == 2 and bool(np.isfinite(vertices).all())
        checks[label + "_boundaries_finite_inside_image"] = bool(valid and ((vertices >= 0).all(axis=1)
                                                                                & (vertices <= image_extent).all(axis=1)).all())
        if label == "cell":
            roi_boundaries = [points for cell_id, points in grouped.items() if cell_id in roi_ids]
    source_hashes = {name: sha256_file(path) for name, path in files.items()}
    source_hashes["morphology_focus.ome.tif"] = sha256_file(image_path)
    archive = None
    if spec.get("archive"):
        archive = verify_archive(spec["archive"], spec["archive_prefix"],
                                 {**files, "morphology_focus.ome.tif": image_path})
        checks["archive_members_found"] = archive["all_members_found"]
        checks["archive_member_hashes_match"] = archive["all_hashes_match"]
        checks["source_archive_unchanged"] = archive["source_unchanged"]
    distance, nearby = tree.query(xy[anchor], k=min(6, len(cells)))
    nearest = tree.query(xy, k=2)[0][:, 1]
    radius_counts = tree.query_ball_point(xy, r=50, return_length=True) - 1
    genes = [gene for gene in ["EPCAM", "SFTPC", "AGER", "PTPRC", "PECAM1", "COL1A1"]
             if ((names == gene) & gene_mask).sum() == 1]
    values = {gene: matrix.getrow(int(np.flatnonzero((names == gene) & gene_mask)[0])).toarray().ravel() for gene in genes}
    with (out / "cell_spatial_index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cell_id", "x_um", "y_um", "x_image_px", "y_image_px", "in_demo_roi", "nearest_other_cell_um", "neighbors_within_50um"] + genes)
        for i, cell_id in enumerate(ids):
            writer.writerow([cell_id, *xy[i], *(xy[i] / pixel_size if checks["pixel_size_agrees_with_ome"] else ("", "")), bool(roi_mask[i]), nearest[i], int(radius_counts[i])]
                            + [int(values[gene][i]) for gene in genes])
    neighbors = [{"cell_id": str(ids[j]), "distance_um": float(d), "x_um": float(xy[j, 0]), "y_um": float(xy[j, 1])}
                 for d, j in zip(distance[1:], nearby[1:])]
    # A plot is a local artifact; only its existence and numeric summary reach the LLM.
    plot_preview(out, preview, image_extent, xy, roi_mask, roi_boundaries, anchor, neighbors, ids, values, spec["sample_id"])
    report = {"schema_version": 1, "validator": "xenium_correspondence_v1", "sample_id": spec["sample_id"],
              "platform": "Xenium", "source_url": spec.get("source_url"),
              "created_at": datetime.now(timezone.utc).isoformat(),
              "status": "correspondence_passed" if all(checks.values()) else "correspondence_failed",
              "cells": len(cells), "matrix_shape_features_by_cells": list(shape),
              "feature_type_counts": dict(Counter(types)), "checks": checks,
              "original_matrix_order_identical": original_order, "image": image,
              "coordinate_unit": "um", "pixel_size_um": pixel_size,
              "image_mapping": {"frame": "native_morphology_full_resolution", "origin": "top_left",
                                "formula": "xy_image_px = xy_um / pixel_size_um",
                                "applicable": checks["pixel_size_agrees_with_ome"]},
              "centroids_inside_image": int(inside.sum()),
              "roi": {"center_um": xy[anchor].tolist(), "radius_um": 150, "cells": int(roi_mask.sum()), "pathology_region": False},
              "anchor_cell_id": str(ids[anchor]), "five_nearest_neighbors": neighbors,
              "median_nearest_neighbor_um": float(np.median(nearest)),
              "displayed_genes": genes, "source_sha256": source_hashes,
              "archive_provenance": archive, "missing_optional_files": optional_missing,
              "limitations": ["Internal correspondence is not independent biological/segmentation ground truth.",
                              "Demo ROI is geometric, not a pathology annotation; neighbors do not prove communication.",
                              "No pathology region assignment or H&E registration accuracy measured."]
                              + ([] if archive else ["Archive identity not checked; physical sample provenance remains unverified."])}
    (out / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def plot_preview(out, preview, extent, xy, roi_mask, boundaries, anchor, neighbors, ids, values, sample_id):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Circle

    lo, hi = np.percentile(preview, [1, 99.7])
    gray = np.clip((preview.astype(float) - lo) / max(float(hi - lo), 1), 0, 1) ** 0.7
    image_extent = (0, float(extent[0]), float(extent[1]), 0)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), constrained_layout=True)
    axes[0].imshow(gray, cmap="gray", extent=image_extent)
    axes[0].scatter(*xy.T, s=0.4, color="cyan", alpha=0.4, linewidths=0)
    axes[0].add_patch(Circle(xy[anchor], 150, fill=False, color="orange"))
    axes[0].set_title(f"{sample_id}: {len(ids):,} cell centroids")
    gene = next(iter(values), None)
    axes[1].scatter(*xy.T, s=1, c="#cccccc", linewidths=0)
    if gene:
        positive = values[gene] > 0
        points = axes[1].scatter(*xy[positive].T, s=2, c=np.log1p(values[gene][positive]), cmap="magma", linewidths=0)
        fig.colorbar(points, ax=axes[1], label="log(1+raw counts)", shrink=0.6)
    axes[1].set_title(f"{gene or 'No demo gene'} at matched cell coordinates")
    axes[1].set_xlim(0, extent[0]); axes[1].set_ylim(extent[1], 0); axes[1].set_aspect("equal")
    axes[2].imshow(gray, cmap="gray", extent=image_extent)
    if boundaries:
        axes[2].add_collection(LineCollection(boundaries, colors="cyan", linewidths=0.4))
    axes[2].scatter(*xy[roi_mask].T, s=3, color="orange", linewidths=0)
    axes[2].add_patch(Circle(xy[anchor], 150, fill=False, color="orange"))
    for row in neighbors:
        axes[2].plot([xy[anchor, 0], row["x_um"]], [xy[anchor, 1], row["y_um"]], color="yellow", lw=1)
    axes[2].set_xlim(xy[anchor, 0]-150, xy[anchor, 0]+150)
    axes[2].set_ylim(xy[anchor, 1]+150, xy[anchor, 1]-150)
    axes[2].set_title(f"150 um demo ROI: {int(roi_mask.sum())} cells")
    for ax in axes:
        ax.set_xlabel("X / um"); ax.set_ylabel("Y / um")
    fig.savefig(Path(out) / "spatial_preview.png", dpi=130)
    plt.close(fig)
