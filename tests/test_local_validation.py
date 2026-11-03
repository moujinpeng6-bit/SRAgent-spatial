import asyncio
import gzip
import importlib.util
import json
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.local_validation import LocalValidationSession, local_validation_tools, run_validation_agent

READY = all(importlib.util.find_spec(name) for name in ["numpy", "scipy", "h5py", "tifffile"])


@unittest.skipUnless(READY, "Install optional xenium dependencies")
class XeniumValidationTests(unittest.TestCase):
    def fixture(self, root, order=(0, 1, 2), duplicate=False, outside=False, bad_counts=False, archive=False):
        import h5py
        import numpy as np
        import tifffile
        from scipy.sparse import csc_matrix

        data = root / "input"
        data.mkdir()
        image = data / "morphology_focus.ome.tif"
        tifffile.imwrite(image, np.ones((32, 32), dtype=np.uint16), ome=True,
                         metadata={"axes": "YX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5,
                                   "PhysicalSizeXUnit": "µm", "PhysicalSizeYUnit": "µm"})
        (data / "experiment.xenium").write_text(json.dumps({"num_cells": 3, "pixel_size": 0.5}))
        with gzip.open(data / "cells.csv.gz", "wt") as f:
            f.write("cell_id,x_centroid,y_centroid,transcript_counts,total_counts\n")
            f.write(f"a,{100 if outside else 1},1,{99 if bad_counts else 1},1\n")
            f.write(f"{'a' if duplicate else 'b'},2,1,3,4\nc,3,2,2,2\n")
        matrix = csc_matrix(np.array([[1, 0, 2], [0, 3, 0], [0, 1, 0]], dtype=np.int32))[:, list(order)]
        with h5py.File(data / "cell_feature_matrix.h5", "w") as f:
            m = f.create_group("matrix")
            for key in ["data", "indices", "indptr", "shape"]:
                m.create_dataset(key, data=getattr(matrix, key))
            m.create_dataset("barcodes", data=np.array(["a", "b", "c"], dtype="S")[list(order)])
            features = m.create_group("features")
            features.create_dataset("name", data=np.array(["EPCAM", "SFTPC", "control"], dtype="S"))
            features.create_dataset("feature_type", data=np.array(["Gene Expression", "Gene Expression", "Negative Control Probe"], dtype="S"))
        spec = {"sample_id": "GSM999001", "platform": "Xenium", "coordinate_unit": "um",
                "data_dir": str(data), "image": str(image)}
        if archive:
            path = root / "bundle.tar.gz"
            with tarfile.open(path, "w:gz") as f:
                for file in data.iterdir():
                    f.add(file, arcname="bundle/outs/" + file.name)
            spec.update(archive=str(path), archive_prefix="bundle/outs")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "samples": [spec]}))
        return spec, manifest

    def validate(self, spec, out):
        from sragent_spatial.xenium_validation import validate_xenium
        with patch("sragent_spatial.xenium_validation.plot_preview"):
            return validate_xenium(spec, out)

    def test_reordered_barcodes_are_reconciled_by_id_and_controls_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self.fixture(root, order=(2, 0, 1))
            report = self.validate(spec, root / "out")
            self.assertEqual(report["status"], "correspondence_passed")
            self.assertFalse(report["original_matrix_order_identical"])
            self.assertEqual(report["feature_type_counts"]["Gene Expression"], 2)
            self.assertEqual(report["centroids_inside_image"], 3)
            self.assertAlmostEqual(report["five_nearest_neighbors"][0]["distance_um"], 1)
            self.assertIsNone(report["archive_provenance"])
            self.assertEqual(len(report["missing_optional_files"]), 2)

    def test_bad_count_or_coordinate_does_not_pass(self):
        for changes, check in [({"outside": True}, "all_centroids_inside_image"),
                               ({"bad_counts": True}, "gene_counts_equal_cells_summary")]:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                spec, _ = self.fixture(root, **changes)
                report = self.validate(spec, root / "out")
                self.assertEqual(report["status"], "correspondence_failed")
                self.assertFalse(report["checks"][check])

    def test_duplicate_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self.fixture(root, duplicate=True)
            with self.assertRaisesRegex(ValueError, "identities"):
                self.validate(spec, root / "out")

    def test_archive_hashes_detect_mutated_local_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, _ = self.fixture(root, archive=True)
            good = self.validate(spec, root / "good")
            self.assertEqual(good["status"], "correspondence_passed")
            path = Path(spec["data_dir"]) / "experiment.xenium"
            path.write_text(path.read_text() + " ")
            bad = self.validate(spec, root / "bad")
            self.assertEqual(bad["status"], "correspondence_failed")
            self.assertFalse(bad["checks"]["archive_member_hashes_match"])

    def test_windows_large_image_product_cannot_select_full_slide(self):
        import numpy as np
        from sragent_spatial.xenium_validation import read_preview
        full = types.SimpleNamespace(shape=(4, 17098, 51187), asarray=lambda: self.fail("Full slide must not be decoded"))
        small = types.SimpleNamespace(shape=(4, 10, 10), asarray=lambda: np.zeros((4, 10, 10), dtype=np.uint16))
        series = types.SimpleNamespace(shape=full.shape, axes="CYX", levels=[full, small])
        fake = types.SimpleNamespace(series=[series], ome_metadata='<OME><Pixels PhysicalSizeX="0.2125" PhysicalSizeY="0.2125"/></OME>')
        with patch("tifffile.TiffFile") as reader:
            reader.return_value.__enter__.return_value = fake
            preview, info = read_preview("unused.tif")
        self.assertEqual(info["pyramid_level"], 1)
        self.assertEqual(preview.shape, (10, 10))

    def test_registry_rejects_path_injection_and_output_under_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            spec, manifest = self.fixture(root)
            with self.assertRaises(ValueError):
                LocalValidationSession(manifest, Path(spec["data_dir"]) / "out")
            spec["sample_id"] = "../outside"
            manifest.write_text(json.dumps({"schema_version": 1, "samples": [spec]}))
            with self.assertRaises(ValueError):
                LocalValidationSession(manifest, root / "out")

    def test_prose_only_supervisor_cannot_claim_success(self):
        class ProseGraph:
            async def ainvoke(self, payload, config):
                return {"messages": [types.SimpleNamespace(content="All samples passed", tool_calls=[])]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, manifest = self.fixture(root)
            session = LocalValidationSession(manifest, root / "out", "gpt-test")
            with patch("sragent_spatial.agents.provider", return_value="openai"):
                report = asyncio.run(run_validation_agent(session, supervisor_factory=lambda _: ProseGraph()))
            self.assertTrue(report["model_invocation_succeeded"])
            self.assertEqual(report["status"], "incomplete")
            self.assertFalse(report["agent_tool_loop_verified"])
            self.assertEqual(report["samples_validated"], 0)

    def test_unregistered_sample_cannot_access_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, manifest = self.fixture(root)
            session = LocalValidationSession(manifest, root / "out")
            result = asyncio.run(session.validate("../../elsewhere"))
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(session.results, {})

    @unittest.skipUnless(importlib.util.find_spec("langchain_core"), "Install optional agent dependency")
    def test_supervisor_tool_loop_really_writes_validation_and_trace(self):
        from langchain_core.tools import tool
        class ToolGraph:
            def __init__(self, session):
                self.tools = local_validation_tools(session, tool)
            async def ainvoke(self, payload, config):
                await self.tools[0].ainvoke({})
                await self.tools[1].ainvoke({"sample_id": "GSM999001"})
                return {"messages": [types.SimpleNamespace(content="Done", tool_calls=[
                    {"name": "list_local_spatial_samples", "id": "1", "args": {}},
                    {"name": "validate_spatial_sample", "id": "2", "args": {"sample_id": "GSM999001"}}])]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, manifest = self.fixture(root)
            session = LocalValidationSession(manifest, root / "out", "gpt-test")
            with patch("sragent_spatial.xenium_validation.plot_preview"), patch(
                "sragent_spatial.agents.provider", return_value="openai"
            ):
                report = asyncio.run(run_validation_agent(session, supervisor_factory=ToolGraph))
            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["agent_tool_loop_verified"])
            self.assertTrue((root / "out/GSM999001/validation.json").is_file())
            self.assertEqual(report["samples"][0]["checks"]["failed"], [])


if __name__ == "__main__":
    unittest.main()
