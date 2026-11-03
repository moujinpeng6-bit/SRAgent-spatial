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
