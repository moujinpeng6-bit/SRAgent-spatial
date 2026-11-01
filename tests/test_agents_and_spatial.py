import asyncio
import gzip
import json
import struct
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.agents import predict_agent
from sragent_spatial.screening import screen_sample
from sragent_spatial.spatial import validate_visium
from test_screening import packet


class AgentWiringTests(unittest.TestCase):
    def factories(self, p, calls):
        class Original:
            async def ainvoke(self, payload, config):
                calls.append(("original", payload, config))
                return {"messages": [types.SimpleNamespace(content=json.dumps(screen_sample(p)))]}

        class Graph:
            def __init__(self, tools):
                self.tools = tools

            async def ainvoke(self, payload, config):
                sample = json.loads(self.tools[1](p["accession"]))
                self.asserted = sample["accession"] == p["accession"]
                try:
                    self.tools[1]("GSM999002")
                except ValueError:
                    calls.append(("cross_sample_blocked",))
                result = json.loads(self.tools[2](p["accession"]))
                calls.append(("spatial", payload, config))
                return {"messages": [types.SimpleNamespace(content=json.dumps(result))]}

        def original_factory(model_name):
            calls.append(("original_factory", model_name))
            return Original()

        def model_factory(**kwargs):
            calls.append(("model_factory", kwargs))
            return object()

        def graph_factory(model, tools, prompt):
            return Graph(tools)

        return original_factory, model_factory, graph_factory, lambda fn: fn

    def test_original_and_spatial_share_upstream_factory(self):
        p, calls = packet(), []
        with patch("sragent_spatial.agents.configure", return_value=self.factories(p, calls)):
            original = asyncio.run(predict_agent(p, "original_agent", "gpt-test"))
            spatial = asyncio.run(predict_agent(p, "spatial_agent", "gpt-test"))
        self.assertEqual(original["label"], "eligible")
        self.assertEqual(spatial["label"], "eligible")
        self.assertIn(("cross_sample_blocked",), calls)
        self.assertEqual([x[1] for x in calls if x[0] == "original_factory"], ["gpt-test", "gpt-test"])
        self.assertTrue(any(x[0] == "model_factory" and x[1]["max_tokens"] == 1536 for x in calls))

    def test_timeout_is_explicit_failure(self):
        class Slow:
            async def ainvoke(self, payload, config):
                await asyncio.sleep(1)

        factories = (lambda model_name: Slow(), None, None, None)
        with patch("sragent_spatial.agents.configure", return_value=factories):
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(predict_agent(packet(), "original_agent", "gpt-test", timeout=0.001))


class SpatialFileTests(unittest.TestCase):
    def files(self, root, positions):
        (root / "barcodes.tsv").write_text("a\nb\n", encoding="utf-8")
        (root / "positions.csv").write_text(positions, encoding="utf-8")
        (root / "scales.json").write_text('{"tissue_lowres_scalef":0.5}', encoding="utf-8")
        (root / "lowres.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 100, 100))
        return root / "barcodes.tsv", root / "positions.csv", root / "scales.json", root / "lowres.png"

    def test_old_format_and_gzip_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.files(root, "a,1,0,0,10,20\nb,1,0,1,30,40\n")
            result = validate_visium(*paths)
            self.assertTrue(result["basic_checks_passed"])
            self.assertEqual(result["image_bounds_check"]["tested_in_tissue_barcodes"], 2)
            with gzip.open(root / "barcodes.tsv.gz", "wt") as stream:
                stream.write("a\nb\n")
            self.assertTrue(validate_visium(root / "barcodes.tsv.gz", paths[1])["basic_checks_passed"])

    def test_missing_ids_and_duplicate_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.files(root, "a,1,0,0,10,20\na,1,0,0,10,20\n")
            result = validate_visium(*paths)
            self.assertIn("duplicate_position_barcodes", result["issues"])
            self.assertIn("expression_barcodes_missing_from_positions", result["issues"])

    def test_bounds_nonfinite_and_matrix_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self.files(root, "barcode,in_tissue,array_row,array_col,pxl_row_in_fullres,pxl_col_in_fullres\na,1,0,0,500,20\nb,1,0,1,nan,40\n")
            (root / "matrix.mtx").write_text("%%MatrixMarket matrix coordinate integer general\n% comment\n10 3 0\n")
            result = validate_visium(*paths, matrix=root / "matrix.mtx")
            self.assertIn("scaled_coordinates_outside_lowres_image", result["issues"])
            self.assertIn("invalid_coordinates_or_tissue_flags", result["issues"])
            self.assertIn("matrix_column_count_does_not_match_barcodes", result["issues"])


if __name__ == "__main__":
    unittest.main()
