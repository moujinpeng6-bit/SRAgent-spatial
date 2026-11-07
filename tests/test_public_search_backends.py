import io
import json
import unittest
from unittest.mock import patch

from sragent_spatial.ontology_ols import decode_response, search_terms
from sragent_spatial.tenx_index import card_row, discover_indexed_human


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/json"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


class PublicSearchTests(unittest.TestCase):
    def test_ontology_distinguishes_exact_and_vector_and_omits_nonhuman(self):
        exact = {"response":{"docs":[{"obo_id":"MONDO:0000001","label":"disease","iri":"urn:exact"}]}}
        vectors = {"items":[
            {"curie":"MONDO:1000001","label":["disease, non-human animal"],"iri":"urn:animal"},
            {"curie":"MONDO:1000002","label":["human disease"],"iri":"urn:human"},
        ]}
        with patch("sragent_spatial.ontology_ols.urlopen", return_value=Response(json.dumps(exact).encode())), \
                patch("sragent_spatial.ontology_ols.OlsVectorClient.call", return_value=vectors):
            report = search_terms("mondo","disease")
        self.assertEqual(report["vector_status"],"ok")
        self.assertEqual([(t["id"],t["method"]) for t in report["terms"]],
                         [("MONDO:0000001","exact_label_or_synonym"),("MONDO:1000002","vector")])

    def test_ols_accepts_json_rpc_event_stream(self):
        payload = Response(b'event: message\ndata: {"id":1,"result":"other"}\n\ndata: {"id":2,"result":"answer"}\n',
                           "text/event-stream")
        self.assertEqual(decode_response(payload,2)["result"],"answer")

    def test_10x_keeps_independent_human_metadata_and_never_guesses_asset_links(self):
        row = {"url":"https://www.10xgenomics.com/datasets/human-new-study","title":"Visium HD Human Kidney",
               "content":"Number of samples on this datasets page 1\nSpecies\n\nHuman\n\nAnatomical Entity\n\nKidney\n\nDisease State\n\nnon-diseased"}
        record = card_row(row,"kidney","Visium HD")
        self.assertEqual(record["state"],"healthy")
        self.assertEqual(record["archives"],{})
        mixed = {**row,"content":row["content"].replace("Human","Mouse, Human")}
        impostor = {**row,"url":"https://example.com/datasets/human-new-study"}
        with patch("sragent_spatial.tenx_index.lookup_public_cards",return_value=[mixed,impostor,row,row]):
            matches = discover_indexed_human("kidney","Visium HD",2)
        self.assertEqual([r["sample_id"] for r in matches],["human-new-study"])
