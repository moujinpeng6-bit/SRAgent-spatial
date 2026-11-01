"""Small fictional SOFT fixtures; no real biomedical results or live requests."""

import asyncio
import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.geo import (
    MetadataClient, asset_kind, build_query, checked_accession, export_records,
    lookup, parse_soft, record_from_soft, source_url, write_json,
)
from sragent_spatial.local import compare_sources, inspect_local
from sragent_spatial.upstream import agent_lookup

SERIES = """^SERIES = GSE999001
!Series_title = Fictional lung spatial pilot [Xenium]
!Series_summary = Study includes disease and healthy controls.
!Series_sample_id = GSM999001
!Series_sample_id = GSM999002
!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/test/image.tif.gz
!Series_supplementary_file = ftp://ftp.ncbi.nlm.nih.gov/geo/test/image.tif.gz
"""
SAMPLE = """^SAMPLE = GSM999001
!Sample_title = Fictional control
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = tissue: lung
!Sample_characteristics_ch1 = disease status: healthy
!Sample_series_id = GSE999001
"""


class PipelineTests(unittest.TestCase):
    def test_no_disease_or_platform_inheritance(self):
        client = types.SimpleNamespace(soft=lambda acc: SERIES if acc.startswith("GSE") else SAMPLE)
        records, scope = lookup(client, "GSE999001", 1)
        self.assertEqual(records[0]["platform"], "Xenium")
        self.assertIsNone(records[0]["disease"])
        self.assertEqual(records[1]["platform"], "unknown")
        self.assertEqual(records[1]["disease"], "healthy")
        self.assertFalse(scope["all_sample_records_fetched"])
        self.assertEqual(scope["sample_records_available"], 2)
        self.assertEqual(len(records[0]["assets"]), 1)

    def test_mixed_assay_is_ambiguous(self):
        text = SERIES + "!Series_summary = Xenium with Visium validation\n"
        record = record_from_soft(parse_soft(text, "GSE999001"), "GSE999001")
        self.assertEqual(record["platform"], "mixed_or_ambiguous")

    def test_wrong_record_or_html_is_rejected(self):
        for text in [SERIES, "<html>temporary outage</html>"]:
            with self.assertRaises(ValueError):
                parse_soft(text, "GSE12345")

    def test_accession_validation(self):
        self.assertEqual(checked_accession(" gse250346 "), "GSE250346")
        for value in ["GSE1&form=html", "SRX123", "GSE0", "../../file"]:
            with self.assertRaises(ValueError):
                checked_accession(value)

    def test_spatial_query_does_not_require_paired_illumina(self):
        query = build_query("Homo sapiens", "lung", "Xenium")
        self.assertIn("gse[ETYP]", query)
        self.assertNotIn("Illumina", query)
        self.assertNotIn("paired", query)
        with self.assertRaises(ValueError):
            build_query('human" OR mouse', "lung", "Xenium")

    def test_cache_replay_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            client = MetadataClient(Path(folder))
            with patch.object(client, "_download", return_value=(SERIES.encode(), "test")):
                client.soft("GSE999001")
            offline = MetadataClient(Path(folder), offline=True)
            self.assertEqual(offline.soft("GSE999001"), SERIES)
            self.assertTrue(offline.requests[0]["cached"])
            next(Path(folder).glob("*.bin")).write_bytes(b"corrupted")
            with self.assertRaises(ValueError):
                offline.soft("GSE999001")

    def test_failed_page_is_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            client = MetadataClient(Path(folder))
            with patch.object(client, "_download", return_value=(b"<html>blocked</html>", "test")):
                with self.assertRaises(ValueError):
                    client.soft("GSE999001")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_cache_miss_and_conflicting_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                MetadataClient(Path(folder), offline=True).soft("GSE999001")
            with self.assertRaises(ValueError):
                MetadataClient(Path(folder), offline=True, refresh=True)

    def test_local_partial_files_never_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "image.tif.gz.part").write_bytes(b"partial")
            url = "https://ftp.ncbi.nlm.nih.gov/geo/test/image.tif.gz"
            write_json(root / "download_manifest.json", [{"relative": "image.tif.gz", "url": url}])
            inventory = inspect_local(root)
            record = record_from_soft(parse_soft(SERIES, "GSE999001"), "GSE999001")
            report = compare_sources([record], inventory)
            match = report["assets"][0]["matches"][0]
            self.assertEqual(match["basis"], "source_url_match")
            self.assertTrue(match["incomplete"])
            self.assertEqual(record["status"], "candidate")

    def test_inventory_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for index in range(3):
                (root / str(index)).write_bytes(b"x")
            report = inspect_local(root, max_files=2)
            self.assertTrue(report["truncated"])
            self.assertEqual(report["file_count"], 2)

    def test_export_preserves_unicode_and_protects_spreadsheet(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record = record_from_soft(parse_soft(SERIES, "GSE999001"), "GSE999001")
            record["title"] = "=unsafe()"
            record["tissue"] = "肺"
            export_records(root, [record], {}, types.SimpleNamespace(requests=[]))
            self.assertEqual(json.loads((root / "records.json").read_text(encoding="utf-8"))[0]["tissue"], "肺")
            self.assertIn("'=unsafe()", (root / "records.csv").read_text(encoding="utf-8-sig"))

    def test_upstream_bridge_calls_original_interface(self):
        calls = []

        class FakeAgent:
            async def ainvoke(self, payload):
                calls.append(payload)
                return {"messages": [types.SimpleNamespace(content="test response")]}

        module = types.ModuleType("SRAgent.agents.ncbi_fetch")
        module.create_ncbi_fetch_agent = lambda: FakeAgent()
        with patch.dict(sys.modules, {"SRAgent": types.ModuleType("SRAgent"),
                                     "SRAgent.agents": types.ModuleType("SRAgent.agents"),
                                     "SRAgent.agents.ncbi_fetch": module}):
            result = asyncio.run(agent_lookup("GSE999001"))
        self.assertIn("GSE999001", calls[0]["message"])
        self.assertEqual(result["review_status"], "unreviewed_agent_output")


if __name__ == "__main__":
    unittest.main()
