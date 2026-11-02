"""Reproducible, read-only Xenium spatial demonstration using two E-drive samples.

Run inspect_local_lung.py first. This is a local validation experiment, not a
claim that the search Agent already runs this validation automatically.
"""

import argparse
import csv
import gzip
import hashlib
import json
import math
import tarfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import tifffile
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle
from scipy.sparse import csc_matrix
from scipy.spatial import cKDTree

PROJECT = Path(__file__).resolve().parents[1]
plt.rcParams.update({"font.family": "Microsoft YaHei", "axes.unicode_minus": False,
                     "font.size": 10, "figure.dpi": 150})


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(4 * 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def image_info(path, max_elements=4_000_000):
    """Decode only a small pre-existing pyramid level; never the full slide."""
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        # Python integers avoid np.prod's int32 overflow on Windows / NumPy 1.x.
        level_index = next(i for i, level in enumerate(series.levels)
                           if math.prod(level.shape) <= max_elements)
        level = series.levels[level_index]
        pixels = ET.fromstring(tif.ome_metadata).find(".//{*}Pixels").attrib
        info = {"path": str(path), "full_shape": list(series.shape), "axes": series.axes,
                "pyramid_level": level_index, "preview_shape": list(level.shape),
                "physical_size_x": float(pixels["PhysicalSizeX"]),
                "physical_size_y": float(pixels["PhysicalSizeY"]),
                "ome_unit_x": pixels.get("PhysicalSizeXUnit", "not explicit"),
                "ome_unit_y": pixels.get("PhysicalSizeYUnit", "not explicit")}
        data = level.asarray()
        if level.axes == "CYX":
            data = data[0]
            info["preview_channel"] = 0
        return data, info


def gray_view(image):
    lo, hi = np.percentile(image, [1, 99.7])
    return np.clip((image.astype(float) - lo) / max(float(hi - lo), 1), 0, 1) ** 0.7


def read_boundaries(path):
    grouped = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            grouped[row["cell_id"]].append((float(row["vertex_x"]), float(row["vertex_y"])))
    return grouped


def test_cells(root, out):
    data_dir = out / "GSM7990538"
    experiment = json.loads((data_dir / "experiment.xenium").read_text())
    with gzip.open(data_dir / "cells.csv.gz", "rt") as stream:
        cells = list(csv.DictReader(stream))
    ids = np.array([row["cell_id"] for row in cells])
    xy = np.array([[float(row["x_centroid"]), float(row["y_centroid"])] for row in cells])
    with h5py.File(data_dir / "cell_feature_matrix.h5", "r") as f:
        group = f["matrix"]
        barcodes = group["barcodes"].asstr()[:]
        names = group["features/name"].asstr()[:]
        types = group["features/feature_type"].asstr()[:]
        matrix = csc_matrix((group["data"][:], group["indices"][:], group["indptr"][:]),
                            shape=tuple(group["shape"][:]))
    matrix.check_format(full_check=True)
    checks = {
        "unique_cell_ids": len(set(ids)) == len(ids),
        "unique_matrix_barcodes": len(set(barcodes)) == len(barcodes),
        "cell_id_set_matches_matrix": set(ids) == set(barcodes),
        "cell_id_order_matches_matrix": bool(np.array_equal(ids, barcodes)),
        "finite_coordinates": bool(np.isfinite(xy).all()),
        "experiment_cell_count_matches": experiment["num_cells"] == len(cells),
        "nonnegative_integer_expression": bool((matrix.data >= 0).all()
                                                and (matrix.data == np.floor(matrix.data)).all()),
    }
    if not checks["cell_id_order_matches_matrix"]:
        raise ValueError("This demonstration requires explicit cell ordering reconciliation")
    gene_sum = np.asarray(matrix[types == "Gene Expression"].sum(axis=0)).ravel()
    total_sum = np.asarray(matrix.sum(axis=0)).ravel()
    gene_recorded = np.array([int(row["transcript_counts"]) for row in cells])
    total_recorded = np.array([int(row["total_counts"]) for row in cells])
    checks["gene_counts_equal_cells_summary"] = bool(np.array_equal(gene_sum, gene_recorded))
    checks["all_feature_counts_equal_cells_summary"] = bool(np.array_equal(total_sum, total_recorded))

    image_path = root / "samples/45_Lung/GEO_bundle/images/2100e2c52d_morphology_focus.ome.tif"
    image, info = image_info(image_path, max_elements=12_000_000)
    pixel_size = float(experiment["pixel_size"])
    height, width = info["full_shape"]
    extent = (0, width * pixel_size, height * pixel_size, 0)
    checks["pixel_size_agrees_with_ome"] = bool(np.isclose(pixel_size, info["physical_size_x"])
                                                 and np.isclose(pixel_size, info["physical_size_y"]))
    inside = ((xy[:, 0] >= 0) & (xy[:, 0] < width * pixel_size)
              & (xy[:, 1] >= 0) & (xy[:, 1] < height * pixel_size))
    checks["all_centroids_inside_image"] = bool(inside.all())

    # Check the image really is the focus image in the SAME archive as expression.
    archive = next((root / "samples/45_Lung/GEO_bundle").glob("*.tar.gz"))
    with image_path.open("rb") as stream:
        image_hash = sha256_stream(stream)
    archive_image_hash = None
    archive_before = archive.stat()
    with tarfile.open(archive, "r|gz") as stream:
        for member in stream:
            if member.isfile() and member.name.endswith("/outs/morphology_focus.ome.tif"):
                archive_image_hash = sha256_stream(stream.extractfile(member))
                break
    checks["image_sha256_matches_same_expression_archive"] = image_hash == archive_image_hash
    checks["source_archive_unchanged"] = (archive_before.st_size == archive.stat().st_size
                                            and archive_before.st_mtime_ns == archive.stat().st_mtime_ns)

    boundaries = read_boundaries(data_dir / "cell_boundaries.csv.gz")
    nuclei = read_boundaries(data_dir / "nucleus_boundaries.csv.gz")
    checks["cell_boundary_ids_match"] = set(boundaries) == set(ids)
    checks["nucleus_boundary_ids_match"] = set(nuclei) == set(ids)
    for label, grouped in [("cell", boundaries), ("nucleus", nuclei)]:
        vertices = np.array([point for polygon in grouped.values() for point in polygon])
        checks[label + "_boundary_coordinates_finite"] = bool(np.isfinite(vertices).all())
        checks[label + "_boundaries_inside_image"] = bool(((vertices >= 0).all(axis=1)
            & (vertices[:, 0] <= width * pixel_size) & (vertices[:, 1] <= height * pixel_size)).all())

    tree = cKDTree(xy)
    anchor = int(tree.query(np.median(xy, axis=0))[1])
    distances, neighbor_indices = tree.query(xy[anchor], k=6)
    nearest_distance = tree.query(xy, k=2)[0][:, 1]
    counts_50 = tree.query_ball_point(xy, r=50.0, return_length=True) - 1
    radius = 150.0
    roi_mask = np.linalg.norm(xy - xy[anchor], axis=1) <= radius
    selected_genes = [gene for gene in ["EPCAM", "SFTPC", "AGER", "PTPRC", "PECAM1", "COL1A1"]
                      if gene in names]
    gene_values = {gene: matrix.getrow(int(np.flatnonzero(names == gene)[0])).toarray().ravel()
                   for gene in selected_genes}
    fields = ["cell_id", "x_um", "y_um", "in_demo_roi", "nearest_other_cell_um", "other_cells_within_50um"] + selected_genes
    with (out / "cell_spatial_index.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        for i, cell_id in enumerate(ids):
            writer.writerow([cell_id, *xy[i], bool(roi_mask[i]), nearest_distance[i], int(counts_50[i])]
                            + [int(gene_values[gene][i]) for gene in selected_genes])
    neighbors = [{"anchor_cell_id": str(ids[anchor]), "neighbor_cell_id": str(ids[j]),
                  "distance_um": float(distance), "x_um": float(xy[j, 0]), "y_um": float(xy[j, 1])}
                 for distance, j in zip(distances[1:], neighbor_indices[1:])]
    save_json(out / "nearest_neighbors.json", neighbors)

    background = gray_view(image)
    fig, axes = plt.subplots(2, 2, figsize=(15, 12), constrained_layout=True)
    ax = axes[0, 0]
    ax.imshow(background, cmap="gray", extent=extent)
    ax.scatter(*xy.T, s=0.7, c="#25d9eb", alpha=0.45, linewidths=0)
    ax.add_patch(Circle(xy[anchor], radius, fill=False, color="#ffa822", lw=2))
    ax.set_title("1. 同一切片：DAPI 图像 + 12,578 个细胞质心\n橙圈是演示区域，半径 150 µm")
    ax = axes[0, 1]
    gene = "EPCAM"
    values = gene_values[gene]
    ax.scatter(*xy.T, s=1, c="#d8d8d8", linewidths=0)
    positive = values > 0
    plotted = ax.scatter(*xy[positive].T, s=3, c=np.log1p(values[positive]), cmap="magma", linewidths=0)
    ax.set_xlim(extent[0:2]); ax.set_ylim(extent[2:4]); ax.set_aspect("equal")
    fig.colorbar(plotted, ax=ax, label="log(1 + 原始 EPCAM 计数)", shrink=0.7)
    ax.set_title("2. 从表达矩阵按细胞 ID 取 EPCAM，再放回真实位置\n灰点 = 该细胞未检出 EPCAM；不等于它不是某类细胞")
    ax = axes[1, 0]
    ax.imshow(background, cmap="gray", extent=extent)
    polygons = [boundaries[cell_id] for cell_id in ids[roi_mask] if cell_id in boundaries]
    ax.add_collection(LineCollection(polygons, colors="#44f5bf", linewidths=0.5))
    ax.scatter(*xy[roi_mask].T, s=3, c="#ffb341", linewidths=0)
    ax.add_patch(Circle(xy[anchor], radius, fill=False, color="#ffa822", lw=1))
    ax.set_xlim(xy[anchor, 0] - radius, xy[anchor, 0] + radius)
    ax.set_ylim(xy[anchor, 1] + radius, xy[anchor, 1] - radius)
    ax.set_title(f"3. 按物理区域取细胞：圈内 {roi_mask.sum()} 个\n图像、细胞边界、质心都使用同一套 µm 坐标")
    ax = axes[1, 1]
    ax.imshow(background, cmap="gray", extent=extent)
    ax.scatter(*xy.T, s=9, c="#888888", alpha=0.6, linewidths=0)
    zoom = max(float(distances[-1]) * 1.9, 60.0)
    for rank, (distance, j) in enumerate(zip(distances[1:], neighbor_indices[1:]), 1):
        ax.plot([xy[anchor, 0], xy[j, 0]], [xy[anchor, 1], xy[j, 1]], c="#ffad29", lw=1.4)
        ax.scatter(*xy[j], c="#ffad29", s=40, edgecolors="black", linewidths=0.5)
        ax.annotate(str(rank), xy[j], xytext=(5, 5), textcoords="offset points", color="#ffdf81", fontsize=10)
    ax.scatter(*xy[anchor], c="#28e3ff", s=80, marker="*", edgecolors="black", linewidths=0.5)
    ax.set_xlim(xy[anchor, 0] - zoom, xy[anchor, 0] + zoom)
    ax.set_ylim(xy[anchor, 1] + zoom, xy[anchor, 1] - zoom)
    ax.text(0.02, 0.98, "\n".join(f"邻居{rank}: {distance:.1f} µm" for rank, distance in enumerate(distances[1:], 1)),
            transform=ax.transAxes, va="top", color="#ffdf81", fontsize=9,
            bbox={"facecolor": "black", "alpha": 0.6, "edgecolor": "none"})
    ax.set_title(f"4. 坐标最近的 5 个细胞\n中心 {ids[anchor]}；几何邻近不直接证明细胞通讯")
    for ax in axes.ravel():
        ax.set_xlabel("X / µm"); ax.set_ylabel("Y / µm")
    fig.suptitle("本地肺组织空间验证 | GSM7990538 · Xenium · 同一切片", fontsize=18)
    fig.savefig(out / "lung_spatial_demo.png")
    plt.close(fig)

    result = {"sample": "GSM7990538", "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM7990538",
              "platform": "Xenium", "cells": len(cells), "matrix_shape_features_by_cells": list(matrix.shape),
              "feature_type_counts": dict(Counter(types)), "checks": checks,
              "count_mismatches": {"gene_counts": int((gene_sum != gene_recorded).sum()),
                                    "all_features": int((total_sum != total_recorded).sum())},
              "image": info, "image_sha256": image_hash, "archive_image_sha256": archive_image_hash,
              "coordinate_unit": "um", "pixel_size_um": pixel_size,
              "centroids_inside_image": int(inside.sum()),
              "nearest_neighbor_distance_um": {"median": float(np.median(nearest_distance)),
                                                "min": float(nearest_distance.min()), "max": float(nearest_distance.max())},
              "demo_roi": {"center_um": xy[anchor].tolist(), "radius_um": radius, "cells": int(roi_mask.sum()),
                           "pathology_annotation": False},
              "anchor_cell_id": str(ids[anchor]), "five_nearest_neighbors": neighbors,
              "limitations": ["Single sample demonstration; not all 31 lung records validated",
                              "Geometric neighbor only, no cell type or communication inference",
                              "Pixel bounds and visual overlays do not quantify segmentation accuracy",
                              "No model training or live search Agent call in this local test"]}
    save_json(out / "cell_validation.json", result)
    return result


def test_annotation(root, out):
    folder = root / "samples/26_Lung"
    he, he_info = image_info(folder / "Xenium_V1_humanLung_Cancer_FFPE_he_image.ome.tif")
    dapi, dapi_info = image_info(folder / "morphology_focus/morphology_focus_0000.ome.tif")
    annotation = json.loads((folder / "annotations/Xenium_V1_humanLung_Cancer_FFPE_annotation.geojson").read_text())
    matrix = np.loadtxt(folder / "annotations/Xenium_V1_humanLung_Cancer_FFPE_he_imagealignment.csv", delimiter=",")
    height, width = he_info["full_shape"][:2]
    dapi_height, dapi_width = dapi_info["full_shape"][-2:]
    regions = []
    transformed = []
    fig, axes = plt.subplots(2, 1, figsize=(15, 9), constrained_layout=True)
    axes[0].imshow(np.rot90(he), extent=(0, height, width, 0))
    axes[0].set_title("A. 肺癌样本的 H&E 图像和来源提供的区域标注（图像旋转以便阅读）")
    axes[1].imshow(gray_view(dapi), cmap="gray", extent=(0, dapi_width, dapi_height, 0))
    axes[1].set_title("B. 用配套 3×3 矩阵把相同区域变换到 DAPI 图像\n这是坐标转换演示；未测量独立关键点配准误差")
    for i, feature in enumerate(annotation["features"]):
        polygon = np.array(feature["geometry"]["coordinates"][0], dtype=float)
        props = feature["properties"]
        label = props["name"]
        color = np.array(props.get("color", props.get("classification", {}).get("color", [255, 255, 0]))) / 255
        mapped_h = np.column_stack([polygon, np.ones(len(polygon))]) @ matrix.T
        mapped = mapped_h[:, :2] / mapped_h[:, 2:3]
        transformed.append(mapped)
        he_inside = (polygon[:, 0] >= 0) & (polygon[:, 0] <= width) & (polygon[:, 1] >= 0) & (polygon[:, 1] <= height)
        dapi_inside = (mapped[:, 0] >= 0) & (mapped[:, 0] <= dapi_width) & (mapped[:, 1] >= 0) & (mapped[:, 1] <= dapi_height)
        regions.append({"id": i + 1, "label": label, "vertices": len(polygon),
                        "all_he_vertices_in_bounds": bool(he_inside.all()),
                        "mapped_dapi_vertices_in_bounds": int(dapi_inside.sum()),
                        "he_bbox_pixels": [*polygon.min(axis=0).tolist(), *polygon.max(axis=0).tolist()]})
        axes[0].plot(polygon[:, 1], width - polygon[:, 0], color=color, lw=1.4)
        axes[1].plot(mapped[:, 0], mapped[:, 1], color=color, lw=1.2)
        center = polygon.mean(axis=0)
        axes[0].text(center[1], width - center[0], str(i + 1), color="black", fontsize=9,
                     bbox={"facecolor": "white", "alpha": 0.85, "pad": 1, "edgecolor": "none"})
    axes[0].set_xlabel("原 H&E 的 Y / pixel"); axes[0].set_ylabel("旋转后的 X / pixel")
    axes[1].set_xlabel("Xenium X / pixel"); axes[1].set_ylabel("Xenium Y / pixel")
    axes[0].set_xlim(0, height); axes[0].set_ylim(width, 0)
    axes[1].set_xlim(0, dapi_width); axes[1].set_ylim(dapi_height, 0)
    legend = " | ".join(f"{i + 1}: {f['properties']['name']}" for i, f in enumerate(annotation["features"]))
    fig.suptitle("独立样本 26_Lung：图像 / 组织区域 / 图像参考系\n与 GSM7990538 不是同一切片，不能混合细胞和标注", fontsize=15)
    fig.text(0.01, 0.01, "区域标签来自原始标注，非本程序诊断。编号见报告。", fontsize=9)
    fig.savefig(out / "lung_pathology_regions.png")
    plt.close(fig)
    result = {"sample": "Xenium_V1_humanLung_Cancer_FFPE", "local_folder": str(folder),
              "he_image": he_info, "dapi_image": dapi_info,
              "regions": regions, "region_count": len(regions),
              "matrix": matrix.tolist(), "matrix_interpretation": "HE pixel column vector -> Xenium image pixel column vector",
              "alignment_accuracy": "Not quantified: no independent landmark residuals checked",
              "expression_cell_link": "Not verified: local sample26 lacks cell expression/coordinate files",
              "separate_from_sample45": True}
    save_json(out / "annotation_validation.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("E:/Xenium"))
    parser.add_argument("--out", type=Path, default=PROJECT / "outputs/local_lung_spatial")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    print("Checking cells, matrix, image provenance and geometric neighbors...", flush=True)
    cells = test_cells(args.root, args.out)
    print("Checking independent lung image annotations and coordinate transform...", flush=True)
    annotation = test_annotation(args.root, args.out)
    print(json.dumps({"cells": cells["cells"], "checks": cells["checks"],
                      "annotation_regions": annotation["region_count"]}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
