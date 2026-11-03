from pathlib import Path
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from sragent_spatial.agents import task_prompt
from sragent_spatial.batch_validation import audit_hd_untagged, probe_sample_assets
from sragent_spatial.discovery import DiscoveryRequest, sample_packet
from sragent_spatial.screening import screen_sample
from sragent_spatial.spatial_search import build_spatial_query


def packet(organism, tissue, requested_organism, requested_tissue):
    result = sample_packet({"Sample_organism_ch1": [organism],
                            "Sample_characteristics_ch1": [f"tissue: {tissue}"],
                            "Sample_title": ["Xenium sample"]}, "GSM100")
    result["screen_scope"] = {"organism": requested_organism, "tissue": requested_tissue}
    return result


class OpenScopeTests(unittest.TestCase):
    def test_species_and_tissue_independent(self):
        request = DiscoveryRequest("Mus musculus", "*", ("Xenium",))
        query = build_spatial_query(request.organism, request.tissue, request.platforms)
        self.assertIn('"Mus musculus"[Organism]', query)
        self.assertNotIn("*", query)
        self.assertNotIn("[Organism]", build_spatial_query("*", "*", ["Xenium"]))
        brain = packet("Mus musculus", "brain", "*", "brain")
        self.assertEqual(screen_sample(brain)["label"], "eligible")
        self.assertIn('"tissue": "brain"', task_prompt(brain))
        self.assertEqual(screen_sample(packet("Mus musculus", "brain", "Homo sapiens", "*"))["label"],
                         "out_of_scope")
        self.assertEqual(screen_sample(packet("Mus musculus", "brain", "*", "lung"))["label"], "unknown")

    def test_missing_h5_does_not_reject_untagged_mex(self):
        base = "ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSMnnn/GSM100/suppl/GSM100_test_"
        suffixes = ["barcodes.tsv.gz", "matrix.mtx.gz", "tissue_positions.parquet.gz",
                    "tissue_lowres_image.png.gz", "scalefactors_json.json.gz"]
        row = {"accession": "GSM100", "assets": [{"url": base + suffix} for suffix in suffixes]}
        with patch("sragent_spatial.batch_validation.LocalFileFetcher") as fetcher, patch(
                "sragent_spatial.batch_validation.FileFetcher"):
            fetcher.return_value.fetch.return_value = {"status": "missing_local_file"}
            result = audit_hd_untagged(row, None, Path("out"), 0)
        self.assertEqual(result["status"], "missing_files")
        self.assertEqual(set(result["files"]), {"barcodes", "matrix", "positions", "image", "scalefactors"})

    def test_metadata_probe_never_gets_payload_or_foreign_sample(self):
        prefix = "https://ftp.ncbi.nlm.nih.gov/geo/samples/GSMnnn/"
        row = {"accession": "GSM100", "assets": [{"url": prefix + sample + "/suppl/test.tar.gz"}
                                                for sample in ("GSM101", "GSM100")]}
        with patch("sragent_spatial.batch_validation.build_opener") as factory:
            opener = factory.return_value
            opener.open.side_effect = HTTPError("", 403, "", {}, None)
            result = probe_sample_assets(row)
            self.assertEqual(opener.open.call_args.args[0].get_method(), "HEAD")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "access_failed")
        self.assertEqual(result[0]["downloaded_bytes"], 0)
