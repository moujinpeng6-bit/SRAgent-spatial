import asyncio
import io
import json
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.pipeline import PipelineSession, pipeline_tools, run_pipeline, TOOL_ORDER
from sragent_spatial.screening import screen_sample
from sragent_spatial.xenium_assets import archive_plan, find_archive, prepare_archive, download_archive
import test_local_validation as fixtures

ACC, STUDY = "GSM999001", "GSE999001"
URL = "ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSM999nnn/GSM999001/suppl/GSM999001_bundle.tar.gz"


class Client:
    requests = []
    def get_json(self, endpoint, **params):
        self.requests.append({"endpoint": endpoint, "cached": False})
        if endpoint == "esearch.fcgi":
            return {"esearchresult": {"idlist": ["1"], "count": "1"}}
        return {"result": {"1": {"accession": STUDY}}}
    def soft(self, acc):
        if acc == STUDY:
            return f"^SERIES = {STUDY}\n!Series_title = Lung Xenium\n!Series_sample_id = {ACC}"
        return (f"^SAMPLE = {ACC}\n!Sample_title = VUHD095_Control\n!Sample_series_id = {STUDY}\n"
                "!Sample_organism_ch1 = Homo sapiens\n!Sample_characteristics_ch1 = tissue: Lung\n"
                "!Sample_characteristics_ch1 = age: 64\n!Sample_extract_protocol_ch1 = Xenium assay\n"
                f"!Sample_supplementary_file = {URL}")


async def predictor(packet, method, model):
    assert "age: 64" not in json.dumps(packet)
    return screen_sample(packet)


class ArchivePlanTests(unittest.TestCase):
    def test_download_budget_and_truncated_response_never_create_complete_archive(self):
        plan = {"filename": "sample.tar.gz", "download_url": "https://ftp.ncbi.nlm.nih.gov/sample.tar.gz"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError): download_archive(plan, root, 0)
            response = io.BytesIO(b"abc")
            response.headers = {"Content-Length": "10"}
            response.geturl = lambda: plan["download_url"]
            with patch("sragent_spatial.xenium_assets.urlopen", return_value=response):
                with self.assertRaisesRegex(ValueError, "Incomplete"):
                    download_archive(plan, root, 20)
            self.assertFalse((root / plan["filename"]).exists())
            self.assertTrue((root / (plan["filename"] + ".part")).exists())
    def test_cross_sample_and_ambiguous_links_rejected(self):
        for values in [[URL.replace(ACC, "GSM999002")], [URL, URL.replace("bundle", "second")]]:
            with self.assertRaises(ValueError):
                archive_plan(ACC, {"Sample_supplementary_file": values})

    def test_partial_download_is_not_a_local_match_and_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sample.tar.gz.part").touch()
            self.assertIsNone(find_archive(root, "sample.tar.gz"))
            for name in ["a", "b"]:
                (root / name).mkdir(); (root / name / "sample.tar.gz").touch()
            with self.assertRaises(ValueError):
                find_archive(root, "sample.tar.gz")

    def test_symlink_or_traversal_member_never_extracted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                info = tarfile.TarInfo(ACC + "_bundle/../outs/cells.csv.gz")
                info.size = 1; stream.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                prepare_archive({"sample_id": ACC}, archive, root.parent / (root.name + "_out"), "VUHD095")
            # Test output is a separate temporary sibling, remove only its empty directory.
            (root.parent / (root.name + "_out")).rmdir()


@unittest.skipUnless(fixtures.READY, "Install xenium dependencies")
class SameSamplePipelineTests(unittest.TestCase):
    def fixture(self, root):
        spec, _ = fixtures.XeniumValidationTests().fixture(root)
        data = Path(spec["data_dir"])
        e = data / "experiment.xenium"
        e.write_text(json.dumps({"num_cells": 3, "pixel_size": 0.5, "region_name": "VUHD095"}))
        archive = data / (ACC + "_bundle.tar.gz")
        with tarfile.open(archive, "w:gz") as stream:
            for path in data.iterdir():
                if path != archive:
                    stream.add(path, arcname=ACC + "_bundle/outs/" + path.name)
        return data

    def test_full_tool_chain_generates_manifest_from_geo_and_local_inventory(self):
        from langchain_core.tools import tool
        class Graph:
            def __init__(self, session): self.tools = pipeline_tools(session, tool)
            async def ainvoke(self, payload, config):
                for step in self.tools:
                    await step.ainvoke({})
                return {"messages": [types.SimpleNamespace(content="Done", tool_calls=[{"name": x, "id": x} for x in TOOL_ORDER])]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = self.fixture(root)
            session = PipelineSession(Client(), STUDY, ACC, data, root / "out", "gpt-test", predictor=predictor)
            with patch("sragent_spatial.xenium_validation.plot_preview"), patch("sragent_spatial.agents.provider", return_value="openai"):
                report = asyncio.run(run_pipeline(session, supervisor_factory=Graph))
            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["agent_pipeline_verified"])
            self.assertEqual(report["preparation"]["downloaded_bytes"], 0)
            self.assertTrue((root / "out/auto_manifest.json").exists())
            self.assertEqual(report["validation"]["cells"], 3)
            self.assertEqual(report["validation"]["checks"]["failed"], [])

    def test_cannot_prepare_or_validate_before_sample_is_screened(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = self.fixture(root)
            session = PipelineSession(Client(), STUDY, ACC, data, root / "out", "gpt-test")
            for action in [session.prepare_files, session.validate_sample]:
                with self.assertRaises(ValueError): asyncio.run(action())

    def test_sample_must_be_listed_in_actual_search_study(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = self.fixture(root)
            client = Client()
            original = client.soft
            client.soft = lambda acc: original(acc).replace("!Series_sample_id = " + ACC, "!Series_sample_id = GSM999002")
            session = PipelineSession(client, STUDY, ACC, data, root / "out", "gpt-test")
            with self.assertRaises(ValueError): asyncio.run(session.search_study())

    def test_prose_only_cannot_complete_the_pipeline(self):
        class Graph:
            async def ainvoke(self, payload, config):
                return {"messages": [types.SimpleNamespace(content="All passed", tool_calls=[])]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = self.fixture(root)
            session = PipelineSession(Client(), STUDY, ACC, data, root / "out", "gpt-test")
            report = asyncio.run(run_pipeline(session, supervisor_factory=lambda _: Graph()))
            self.assertEqual(report["status"], "incomplete")
            self.assertFalse(report["agent_pipeline_verified"])
