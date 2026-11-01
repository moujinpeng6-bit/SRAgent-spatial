import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.evaluation import benchmark
from sragent_spatial.screening import parse_prediction, screen_sample


def packet(tissue="lung", protocol="Visium library preparation", organism="Homo sapiens"):
    return {"accession": "GSM999001", "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM999001",
            "fields": {"Sample_organism_ch1": [organism], "Sample_characteristics_ch1": ["tissue: " + tissue],
                       "Sample_extract_protocol_ch1": [protocol]}}


class ScreeningTests(unittest.TestCase):
    def test_positive_and_hard_negative_organs(self):
        self.assertEqual(screen_sample(packet())["label"], "eligible")
        self.assertEqual(screen_sample(packet("right ventricle (transmural)", "Xenium slides"))["label"], "non_lung")
        self.assertEqual(screen_sample(packet("larynx; hypopharynx", "Xenium"))["label"], "non_lung")

    def test_nonspatial_sample_not_rescued_by_study(self):
        p = packet(protocol="Chromium single-cell RNA-seq")
        p["series_title"] = "Lung Visium and Xenium atlas"
        self.assertEqual(screen_sample(p)["label"], "unknown")

    def test_no_tissue_inference_from_title(self):
        p = packet()
        p["fields"].pop("Sample_characteristics_ch1")
        p["fields"]["Sample_title"] = ["lung cancer related genes"]
        self.assertEqual(screen_sample(p)["label"], "unknown")

    def test_mixed_organ_and_platform(self):
        self.assertEqual(screen_sample(packet("lung; breast"))["label"], "mixed")
        self.assertEqual(screen_sample(packet(protocol="Visium and Xenium"))["label"], "mixed")

    def test_species_and_negation(self):
        self.assertEqual(screen_sample(packet(organism="Mus musculus"))["label"], "unknown")
        self.assertNotEqual(screen_sample(packet("non-lung tissue"))["label"], "eligible")

    def test_identity_and_frozen_evidence_checks(self):
        p = packet()
        p["accession"] = "GSE999001"
        with self.assertRaises(ValueError):
            screen_sample(p)
        p = packet()
        p["fields_sha256"] = "wrong"
        with self.assertRaises(ValueError):
            screen_sample(p)

    def test_model_cannot_invent_quote_or_cite_parent(self):
        p = packet()
        prediction = screen_sample(p)
        parsed = parse_prediction(json.dumps(prediction), p)
        self.assertTrue(parsed["citation_validated"])
        for edit in ["quote", "source_url"]:
            bad = copy.deepcopy(prediction)
            bad["evidence"][0][edit] = "fabricated" if edit == "quote" else p["source_url"].replace("GSM", "GSE")
            with self.assertRaises(ValueError):
                parse_prediction(json.dumps(bad), p)

    def test_missing_models_are_not_zero_percent_results(self):
        dataset = {"reference_status": "assistant_prepared_pending_human_review",
                   "cases": [{"input": packet(), "reference": {"label": "eligible"}}]}
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(benchmark(dataset, ["rules", "original_agent", "spatial_agent"]))
        self.assertEqual(result["summary"]["rules"]["completed"], 1)
        self.assertIsNone(result["summary"]["original_agent"]["provisional_reference_agreement"])
        self.assertIsNone(result["summary"]["original_agent"]["agreement_all_requested_cases"])

    def test_benchmark_never_passes_reference_to_model(self):
        received = []

        async def fake_predictor(p, method, model):
            self.assertNotIn("reference", p)
            received.append(p["accession"])
            return screen_sample(p)

        dataset = {"reference_status": "assistant_prepared_pending_human_review",
                   "cases": [{"input": packet(), "reference": {"label": "eligible", "reason": "SECRET_LABEL"}}]}
        result = asyncio.run(benchmark(dataset, ["original_agent", "spatial_agent"], "test-model", predictor=fake_predictor))
        self.assertEqual(received, ["GSM999001", "GSM999001"])
        self.assertEqual(result["summary"]["spatial_agent"]["completed"], 1)


if __name__ == "__main__":
    unittest.main()
