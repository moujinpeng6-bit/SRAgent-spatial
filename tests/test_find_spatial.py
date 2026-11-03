import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.batch_validation import validate_candidates
from sragent_spatial.find_spatial import cache_samples


class FindSpatialTests(unittest.TestCase):
    def test_human_tissue_matches_manifest_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            catalog = {"samples": [
                {"row": 1, "source": "10X", "organ": "Kidney", "dataset": "Human Kidney"},
                {"row": 2, "source": "10X", "organ": "Kidney", "dataset": "Mouse Kidney"},
                {"row": 3, "source": "10X", "organ": "Pancreas", "dataset": "Human Pancreas"},
            ]}
            assets = [{"row": n, "type": "zip", "relative": f"samples/Test{n}/morphology_focus.ome.tif"}
                      for n in range(1, 4)]
            (root / "sample_metadata.json").write_text(json.dumps(catalog), encoding="utf8")
            (root / "download_manifest.json").write_text(json.dumps(assets), encoding="utf8")
            self.assertEqual(cache_samples(root, "Homo sapiens", "kidney", ["Xenium"]), ["Test1"])
            self.assertEqual(cache_samples(root, "Homo sapiens", "kidney", ["Visium"]), [])

    def test_geo_resume_skips_checkpointed_sample_and_runs_next(self):
        report = {"request": {"platforms": ["Visium"]}, "samples": [
            {"accession": acc, "status": "completed", "include_in_catalog": True,
             "rule_prediction": {"platform_candidates": ["Visium"]}}
            for acc in ("GSM1", "GSM2")
        ]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = root / "inputs"
            inputs.mkdir()
            out = root / "out"
            out.mkdir()
            (out / "batch.json").write_text(json.dumps({"status": "running", "status_counts": {},
                "samples": [{"sample_id": "GSM1", "status": "correspondence_passed"}]}), encoding="utf8")
            with patch("sragent_spatial.batch_validation.audit_visium_candidate",
                       return_value={"status": "correspondence_passed"}) as audit:
                result = asyncio.run(validate_candidates(report, inputs, out, resume=True))
            self.assertEqual(audit.call_count, 1)
            self.assertEqual(audit.call_args.args[0]["accession"], "GSM2")
            self.assertEqual(result["status_counts"], {"correspondence_passed": 2})
