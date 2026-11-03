import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from sragent_spatial.batch_validation import validate_candidates
from sragent_spatial.xenium_assets import AmbiguousBundle


class BatchValidationTests(unittest.TestCase):
    def test_failures_are_recorded_before_later_success(self):
        samples = [
            dict(accession=f"GSM{i}", status="completed", include_in_catalog=True,
                 studies=["GSE1"], source_series_ids=["GSE1"],
                 rule_prediction={"platform_candidates": ["Xenium"]},
                 packet={"fields": {}}, assets=[])
            for i in range(1, 5)
        ]
        report = {"samples": samples, "request": {"platforms": ["Xenium"]}, "model": "test"}
        prepare = AsyncMock(side_effect=[
            FileNotFoundError(), HTTPError("https://example.org", 403, "", {}, None), AmbiguousBundle(), {},
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inputs").mkdir()
            with patch("sragent_spatial.pipeline.PipelineSession.prepare_files", prepare), patch(
                "sragent_spatial.pipeline.PipelineSession.validate_sample",
                AsyncMock(return_value={"status": "correspondence_passed"}),
            ):
                result = asyncio.run(validate_candidates(report, root / "inputs", root / "out"))
        self.assertEqual([row["status"] for row in result["samples"]],
                         ["missing_files", "access_failed", "ambiguous_bundle", "correspondence_passed"])
        self.assertEqual(result["samples"][1]["http_status"], 403)

    def test_saved_candidate_reuses_real_preparation_and_exports_pixel_coordinates(self):
        import csv
        import test_pipeline_agent as fixture

        if not fixture.fixtures.READY:
            self.skipTest("Install Xenium dependencies")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = fixture.SameSamplePipelineTests().fixture(root)
            sample = fixture.ACC
            study = fixture.STUDY
            report = {"request": {"platforms": ["Xenium"]}, "model": "test", "samples": [
                dict(accession=sample, status="completed", include_in_catalog=True,
                     studies=[study], source_series_ids=[study],
                     rule_prediction={"platform_candidates": ["Xenium"]},
                     packet={"fields": {"Sample_title": ["VUHD095_Control"]}},
                     assets=[{"url": fixture.URL}])
            ]}
            with patch("sragent_spatial.xenium_validation.plot_preview"):
                result = asyncio.run(validate_candidates(report, inputs, root / "batch"))
            self.assertEqual(result["status_counts"], {"correspondence_passed": 1})
            index = root / "batch" / sample / "validation" / sample / "cell_spatial_index.csv"
            with index.open(encoding="utf-8", newline="") as stream:
                cell = next(csv.DictReader(stream))
            self.assertEqual(cell["cell_id"], "a")
            self.assertEqual((float(cell["x_image_px"]), float(cell["y_image_px"])), (2.0, 2.0))

    def test_visium_candidate_checks_local_files_and_hd_is_not_routed_as_visium(self):
        import importlib.util
        from test_asset_audit import LocalFiles, sample_record
        from sragent_spatial.asset_audit import file_plan

        if not importlib.util.find_spec("PIL"):
            self.skipTest("Install the validation extra")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            LocalFiles(inputs)
            sample = sample_record()
            sample["rule_prediction"] = {"platform_candidates": ["Visium"]}
            sample["packet"] = {"fields": {}}
            for role, asset in file_plan(sample)["roles"].items():
                (inputs / (role + ".gz")).rename(inputs / asset["filename"])
            hd = sample_record("GSM999002")
            hd["rule_prediction"] = sample["rule_prediction"]
            hd["packet"] = {"fields": {"Sample_title": ["Human lung Visium HD"]}}
            report = {"request": {"platforms": ["Visium"]}, "samples": [sample, hd]}
            result = asyncio.run(validate_candidates(report, inputs, root / "batch"))
        self.assertEqual(result["status_counts"], {"correspondence_passed": 1, "missing_files": 1})
        checks = result["samples"][0]["validation"]["checks"]
        self.assertEqual(checks["barcode_position_image"]["image_bounds_check"]["tested_in_tissue_barcodes"], 2)
        self.assertTrue(checks["image_decode"]["pixel_decode_verified"])

    def test_visium_hd_keeps_parquet_and_barcodes_separate_by_bin(self):
        import gzip
        import importlib.util
        from test_asset_audit import LocalFiles, sample_record
        from sragent_spatial.asset_audit import file_plan

        if not all(importlib.util.find_spec(name) for name in ("PIL", "pyarrow")):
            self.skipTest("Install the Visium HD extra")
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            inputs.mkdir()
            sample = sample_record(suffix="section1_002um")
            sample["assets"] = []
            sample["rule_prediction"] = {"platform_candidates": ["Visium"]}
            sample["packet"] = {"fields": {"Sample_title": ["Human lung Visium HD"]}}
            for size in (2, 8, 16):
                assets = sample_record(suffix=f"section1_{size:03d}um")["assets"]
                for asset in assets:
                    asset["url"] = asset["url"].replace("tissue_positions_list.csv.gz", "tissue_positions.parquet.gz")
                sample["assets"].extend(assets)
                fixture = root / str(size)
                fixture.mkdir()
                LocalFiles(fixture)
                ids = [f"s_{size:03d}um_00000_00000-1", f"s_{size:03d}um_00000_00001-1"]
                expression_ids = ids if size != 16 else [barcode.replace("016um", "008um") for barcode in ids]
                (fixture / "barcodes.gz").write_bytes(gzip.compress(("\n".join(expression_ids) + "\n").encode()))
                pq.write_table(pa.table({"barcode": ids, "in_tissue": [1, 1], "array_row": [0, 0],
                    "array_col": [0, 1], "pxl_row_in_fullres": [10., 30.],
                    "pxl_col_in_fullres": [20., 40.]}), fixture / "positions.parquet")
                (fixture / "positions.gz").write_bytes(gzip.compress((fixture / "positions.parquet").read_bytes()))
                for role, asset in file_plan(sample, bin_um=size)["roles"].items():
                    (fixture / (role + ".gz")).rename(inputs / asset["filename"])
            report = {"request": {"platforms": ["Visium HD"]}, "samples": [sample]}
            result = asyncio.run(validate_candidates(report, inputs, root / "batch"))
        row = result["samples"][0]
        self.assertEqual(row["platform"], "Visium HD")
        self.assertEqual({key: value["status"] for key, value in row["bins_um"].items()},
                         {"2": "correspondence_passed", "8": "correspondence_passed", "16": "mismatch"})
        self.assertIn("expression_barcode_bin_size_mismatch", row["bins_um"]["16"]["validation"]["issues"])

    def test_hd_archive_matches_flat_native_bin_files(self):
        import importlib.util
        import json
        import tarfile

        if not all(importlib.util.find_spec(name) for name in ("PIL", "pyarrow", "h5py")):
            self.skipTest("Install Visium HD dependencies")
        import h5py
        import pyarrow as pa
        import pyarrow.parquet as pq
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / "inputs"
            source = root / "A"
            inputs.mkdir()
            source.mkdir()
            barcode = "s_002um_00000_00000-1"
            with h5py.File(source / "A_2um_filtered_feature_bc_matrix.h5", "w") as stream:
                stream["matrix/shape"] = [1, 1]
                stream["matrix/barcodes"] = [barcode.encode()]
            pq.write_table(pa.table({"barcode": [barcode], "in_tissue": [1], "array_row": [0],
                "array_col": [0], "pxl_row_in_fullres": [20.], "pxl_col_in_fullres": [10.]}),
                source / "A_2um_tissue_positions.parquet")
            (source / "A_2um_scalefactors_json.json").write_text(json.dumps({"bin_size_um": 2, "tissue_lowres_scalef": 0.5}))
            Image.new("RGB", (20, 20), "white").save(source / "A_tissue_lowres_image.png")
            with tarfile.open(inputs / "GSM123_A.tar.gz", "w:gz") as stream:
                stream.add(source, arcname="A")
            row = {"accession": "GSM123", "status": "completed", "include_in_catalog": True,
                   "rule_prediction": {"platform_candidates": ["Visium HD"]},
                   "assets": [{"url": "ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSMnnn/GSM123/suppl/GSM123_A.tar.gz"}]}
            report = {"request": {"platforms": ["Visium HD"]}, "samples": [row]}
            result = asyncio.run(validate_candidates(report, inputs, root / "batch"))
        self.assertEqual(result["status_counts"], {"correspondence_passed": 1})
        bin_report = result["samples"][0]["bins_um"]["2"]
        self.assertEqual(bin_report["archive_members"]["image"], "A/A_tissue_lowres_image.png")
        self.assertTrue(bin_report["checks"]["matrix_columns_match_barcodes"])
