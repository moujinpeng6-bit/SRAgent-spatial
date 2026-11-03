import asyncio
import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sragent_spatial.hd_archive import spatial_bin_size, validate_hd_bin
from sragent_spatial.unified_batch import validate_sources


class UnifiedBatchTests(unittest.TestCase):
    def test_declared_24_micrometre_bundle_can_enter_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scalefactors.json"
            path.write_text('{"bin_size_um": 24.0}', encoding="utf8")
            self.assertEqual(spatial_bin_size(path), 24)

    def test_hd_matrix_prefix_uses_gzip_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "matrix.mtx.gz.prefix"
            path.write_bytes(gzip.compress(b"%%MatrixMarket matrix coordinate integer general\n2 1 1\n1 1 5\n"))
            files = {role: {"path": root / role, "member": role}
                     for role in ("barcodes", "positions", "scalefactors", "image")}
            files["matrix"] = {"path": path, "member": "matrix.mtx.gz"}
            with patch("sragent_spatial.hd_archive.validate_visium",
                       return_value={"expression_barcode_count": 1, "issues": []}), patch(
                       "sragent_spatial.hd_archive.decode_image", return_value={}):
                result = validate_hd_bin(files, root, 24)
        self.assertEqual(result["status"], "correspondence_passed")

    def test_deduplicates_geo_and_counts_prepared_separately_from_passed(self):
        hd = {"accession": "GSM1", "status": "completed", "include_in_catalog": True,
              "rule_prediction": {"platform_candidates": ["Visium HD"]}}
        excluded = {"accession": "GSM2", "status": "completed", "include_in_catalog": False,
                    "rule_prediction": {"platform_candidates": ["Visium"]}}
        reports = [{"samples": [hd, excluded]}, {"samples": [hd]}]
        geo = {"samples": [{"sample_id": "GSM1", "platform": "Visium HD", "status": "mismatch",
                            "bins_um": {"8": {"status": "mismatch"}}}]}
        cache = {"samples": {"Skin": {"status": "correspondence_passed", "preparation": {"status": "prepared"}},
                             "Missing": {"status": "missing_source"}}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inputs = root / "inputs"
            inputs.mkdir()
            with patch("sragent_spatial.unified_batch.validate_candidates", AsyncMock(return_value=geo)) as run_geo, patch(
                    "sragent_spatial.unified_batch.run_batch", AsyncMock(return_value=cache)):
                report = asyncio.run(validate_sources(reports, inputs, inputs, ["Skin", "Missing"], root / "out"))
        self.assertEqual(run_geo.await_count, 1)
        self.assertEqual(report["totals"]["found"], 3)
        self.assertEqual(report["totals"]["selected"], 3)
        self.assertEqual(report["totals"]["files_ready"], 2)
        self.assertEqual(report["totals"]["passed"], 1)
        self.assertEqual(report["totals"]["failed"], 2)
        self.assertEqual(report["by_platform"]["Visium"]["selected"], 0)
