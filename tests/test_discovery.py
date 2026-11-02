import asyncio
import csv
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.discovery import DiscoveryRequest, DiscoverySession, build_supervisor, discover
from sragent_spatial.screening import screen_sample


def sample(acc, tissue="lung", platform="Visium", studies=("GSE999001",)):
    return (f"^SAMPLE = {acc}\n!Sample_organism_ch1 = Homo sapiens\n"
            f"!Sample_characteristics_ch1 = tissue: {tissue}\n!Sample_extract_protocol_ch1 = {platform} assay\n"
            + "".join(f"!Sample_series_id = {study}\n" for study in studies))


class Client:
    def __init__(self, records, total=100):
        self.records, self.total, self.requests, self.calls = records, total, [], []

    def soft(self, acc):
        self.calls.append(acc)
        if isinstance(self.records[acc], Exception):
            raise self.records[acc]
        return self.records[acc]

    def get_json(self, endpoint, **params):
        studies = [acc for acc in self.records if acc.startswith("GSE")][:params.get("retmax", 100)]
        if endpoint == "esearch.fcgi":
            return {"esearchresult": {"count": str(self.total), "idlist": studies}}
        return {"result": {acc: {"accession": acc} for acc in studies}}


def series(acc, samples):
    return (f"^SERIES = {acc}\n!Series_title = Lung Visium study\n"
            + "".join(f"!Series_sample_id = {value}\n" for value in samples))


async def predictor(packet, method, model):
    return screen_sample(packet)


class DiscoveryTests(unittest.TestCase):
    def test_resume_only_reads_and_model_checks_previously_unread_samples(self):
        calls = []
        async def counted(packet, method, model):
            calls.append(packet["accession"])
            return screen_sample(packet)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            records = {"GSE999001": series("GSE999001", ["GSM999001", "GSM999002"]),
                       "GSM999001": sample("GSM999001"), "GSM999002": sample("GSM999002")}
            old = self.session(root / "old", records, DiscoveryRequest(max_samples_per_study=1), pred=counted)
            async def initial():
                await old.search(); await old.expand("GSE999001"); await old.screen("GSE999001")
            asyncio.run(initial())
            new = DiscoverySession.resume(Client(records), root / "old/run.json", root / "new", "gpt-test",
                                          max_samples_per_study=50, predictor=counted)
            async def complete():
                await new.expand("GSE999001"); await new.screen("GSE999001")
            asyncio.run(complete())
            self.assertEqual(calls, ["GSM999001", "GSM999002"])
            self.assertEqual(new.client.calls, ["GSM999002"])
            self.assertEqual(new.studies["GSE999001"]["attempted_ids"], ["GSM999001", "GSM999002"])
            self.assertEqual(new.studies["GSE999001"]["not_read_ids"], [])
            self.assertEqual(new.resume_provenance["previous_completed_samples"], 1)
            with self.assertRaises(ValueError):
                DiscoverySession.resume(Client(records), root / "old/run.json", root / "new", "different-model")

    def session(self, root, records, request=None, pred=predictor):
        return DiscoverySession(Client(records), request or DiscoveryRequest(), root, "gpt-test", predictor=pred)

    def test_study_metadata_does_not_turn_heart_or_nonspatial_sample_into_lung_spatial(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001", "GSM999002", "GSM999003"]),
                "GSM999001": sample("GSM999001"), "GSM999002": sample("GSM999002", "heart"),
                "GSM999003": sample("GSM999003", platform="Chromium single-cell RNA-seq")})
            async def run():
                await session.search(); await session.expand("GSE999001"); await session.screen("GSE999001")
            asyncio.run(run())
            self.assertEqual([row["label"] for row in session.samples.values()], ["eligible", "non_lung", "unknown"])
            self.assertEqual(session.report()["counts"]["selected_metadata_candidates"], 1)
            self.assertEqual(session.report()["status"], "completed_with_limits")

    def test_per_study_and_global_limits_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            ids = ["GSM999001", "GSM999002", "GSM999003"]
            session = self.session(folder, {"GSE999001": series("GSE999001", ids), **{acc: sample(acc) for acc in ids}},
                DiscoveryRequest(max_samples_per_study=2, max_total_samples=1))
            async def run():
                await session.search(); await session.expand("GSE999001"); await session.screen("GSE999001")
            asyncio.run(run())
            self.assertEqual(list(session.samples), ["GSM999001"])
            self.assertEqual(session.studies["GSE999001"]["not_read_ids"], ids[1:])
            self.assertNotIn("GSM999002", session.client.calls)

    def test_failed_sample_is_recorded_and_remaining_samples_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001", "GSM999002"]),
                "GSM999001": ValueError("provider text must not enter report"), "GSM999002": sample("GSM999002")})
            async def run():
                await session.search(); await session.expand("GSE999001"); await session.screen("GSE999001")
            asyncio.run(run())
            self.assertEqual(session.samples["GSM999001"]["status"], "read_failed")
            self.assertEqual(session.samples["GSM999002"]["status"], "completed")
            self.assertEqual(session.report()["status"], "incomplete")
            self.assertNotIn("provider text", json.dumps(session.report()))

    def test_xenium_sample_is_not_selected_for_visium_request(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001"]),
                "GSM999001": sample("GSM999001", platform="Xenium")})
            async def run():
                await session.search(); await session.expand("GSE999001"); await session.screen("GSE999001")
            asyncio.run(run())
            row = session.samples["GSM999001"]
            self.assertEqual(row["label"], "eligible")
            self.assertFalse(row["include_in_catalog"])

    def test_duplicate_samples_are_read_and_model_checked_once(self):
        calls = []
        async def counted(packet, method, model):
            calls.append(packet["accession"])
            return screen_sample(packet)
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001"]),
                "GSE999002": series("GSE999002", ["GSM999001"]),
                "GSM999001": sample("GSM999001", studies=("GSE999001", "GSE999002"))}, pred=counted)
            async def run():
                await session.search()
                for study in session.studies:
                    await session.expand(study); await session.screen(study)
            asyncio.run(run())
            self.assertEqual(calls, ["GSM999001"])
            self.assertEqual(session.client.calls.count("GSM999001"), 1)
            self.assertEqual(session.samples["GSM999001"]["studies"], ["GSE999001", "GSE999002"])

    def test_unknown_study_or_missing_parent_association_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001"]),
                "GSM999001": sample("GSM999001", studies=("GSE999099",))})
            async def run():
                await session.search()
                with self.assertRaises(ValueError):
                    await session.expand("GSE999099")
                await session.expand("GSE999001")
            asyncio.run(run())
            self.assertEqual(session.samples["GSM999001"]["status"], "read_failed")

    def test_fabricated_model_citation_is_not_exported_as_completed(self):
        async def fabricated(packet, method, model):
            result = screen_sample(packet)
            result["evidence"][0]["quote"] = "fabricated quote"
            return result
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": series("GSE999001", ["GSM999001"]),
                "GSM999001": sample("GSM999001")}, pred=fabricated)
            async def run():
                await session.search(); await session.expand("GSE999001"); await session.screen("GSE999001")
            asyncio.run(run())
            self.assertEqual(session.samples["GSM999001"]["status"], "screen_failed")
            self.assertEqual(session.report()["counts"]["selected_metadata_candidates"], 0)

    def test_agent_prose_cannot_fabricate_export_rows_or_claim_completion(self):
        class Graph:
            async def ainvoke(self, *args, **kwargs):
                return {"messages": [types.SimpleNamespace(content="All 999 samples are eligible") ]}
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {})
            report = asyncio.run(discover(session, supervisor_factory=lambda s: Graph()))
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["samples"], [])
            self.assertTrue((Path(folder) / "samples.csv").is_file())

    def test_build_extends_actual_supervisor_factory_and_restores_binding(self):
        observed = {}
        def graph_builder(**kwargs):
            observed.update(kwargs)
            return object()
        module = types.SimpleNamespace(create_react_agent=graph_builder)
        def factory(model_name, return_tool):
            self.assertFalse(return_tool)
            return module.create_react_agent(model=model_name, tools=["entrez", "ncbi", "bigquery", "sequences"], prompt="upstream")
        module.create_sragent_agent = factory
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {})
            with patch("sragent_spatial.agents.configure", return_value=(None, None, None, lambda f: f)), \
                    patch("sragent_spatial.agents.provider", return_value="openai"), \
                    patch("sragent_spatial.discovery.importlib.import_module", return_value=module):
                build_supervisor(session)
        self.assertEqual(observed["tools"][:4], ["entrez", "ncbi", "bigquery", "sequences"])
        self.assertEqual([t.__name__ for t in observed["tools"][4:]],
                         ["search_spatial_datasets", "expand_spatial_study", "screen_spatial_study"])
        self.assertIs(module.create_react_agent, graph_builder)
        self.assertIn("upstream", observed["prompt"])

    def test_one_study_fetch_failure_does_not_discard_other_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self.session(folder, {"GSE999001": ValueError("bad study"),
                "GSE999002": series("GSE999002", [])})
            asyncio.run(session.search())
            self.assertEqual(list(session.studies), ["GSE999002"])
            self.assertEqual(session.search_report["study_failures"][0]["accession"], "GSE999001")


if __name__ == "__main__":
    unittest.main()
