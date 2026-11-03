import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from sragent_spatial.batch_validation import validate_candidates


class BatchValidationTests(unittest.TestCase):
    def test_failures_are_recorded_before_later_success(self):
        samples = [
            dict(accession=f"GSM{i}", status="completed", include_in_catalog=True,
                 studies=["GSE1"], source_series_ids=["GSE1"],
                 rule_prediction={"platform_candidates": ["Xenium"]},
                 packet={"fields": {}}, assets=[])
            for i in range(1, 4)
        ]
        report = {"samples": samples, "request": {"platforms": ["Xenium"]}, "model": "test"}
        prepare = AsyncMock(side_effect=[
            FileNotFoundError(), HTTPError("https://example.org", 403, "", {}, None), {},
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
                         ["missing_files", "access_failed", "correspondence_passed"])
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
        self.assertEqual(result["status_counts"], {"correspondence_passed": 1, "unsupported_platform": 1})
        checks = result["samples"][0]["validation"]["checks"]
        self.assertEqual(checks["barcode_position_image"]["image_bounds_check"]["tested_in_tissue_barcodes"], 2)
        self.assertTrue(checks["image_decode"]["pixel_decode_verified"])
