import io
import asyncio
import json
import tempfile
import unittest
import zipfile
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch, Mock

from sragent_spatial.biostudies import file_url, run_biostudies, run_biostudies_agent, study_evidence, study_files
from sragent_spatial.hd_archive import hdf5_count_integrity


class BioStudiesTests(unittest.TestCase):
    def test_agent_must_execute_real_tool_and_does_not_repeat_a_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            report = {"status": "completed", "totals": {}, "samples": []}

            class Graph:
                def __init__(self, tools):
                    self.tools = tools

                async def ainvoke(self, *_args, **_kwargs):
                    await self.tools[0]()
                    await self.tools[0]()

            config = (None, Mock(), lambda **options: Graph(options["tools"]), lambda fn: fn)
            with patch("sragent_spatial.agents.configure", return_value=config), patch(
                "sragent_spatial.agents.provider", return_value="test"
            ), patch("sragent_spatial.biostudies.run_biostudies", return_value=report) as execute:
                result = asyncio.run(run_biostudies_agent("Homo sapiens", "lung", ["Visium"],
                                                         folder, model="test-model"))
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(result["agent"]["tool_calls"], 2)
                self.assertTrue(result["agent"]["actual_tool_executed"])

    def test_source_identity_and_mixed_species(self):
        study = {"section": {"attributes": [
            {"name": "Organism", "value": "Homo sapiens"},
            {"name": "Organism", "value": "Mus musculus"},
            {"name": "Description", "value": "Visium lung sections"}]}}
        self.assertFalse(study_evidence(study, "Homo sapiens", "lung", "Visium")["matches"])
        with self.assertRaises(ValueError):
            file_url({"httpLink": "https://example.org/study"}, "S-BSST100", "data.zip")
        with self.assertRaises(ValueError):
            study_files({"section": {"files": [{"path": "../../outside", "size": 10}]}})

    @unittest.skipUnless(find_spec("h5py") and find_spec("PIL"), "requires validation extras")
    def test_real_zip_checks_and_resumes_slices_after_one_is_incomplete(self):
        import h5py
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            matrix = root / "matrix.h5"
            with h5py.File(matrix, "w") as f:
                m = f.create_group("matrix")
                m["shape"] = [2, 2]
                m["barcodes"] = [b"a", b"b"]
                m["data"] = [3, 5]
                m["indices"] = [0, 1]
                m["indptr"] = [0, 1, 2]
            image = io.BytesIO()
            Image.new("RGB", (10, 10)).save(image, format="PNG")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for name in ("incomplete", "complete", "mismatched"):
                    archive.writestr(name + "/filtered_feature_bc_matrix.h5", matrix.read_bytes())
                    if name == "incomplete":
                        continue
                    coordinates = "a,1,0,0,1,2\nb,1,0,1,2,3\n" if name == "complete" else "other,1,0,0,1,2\n"
                    archive.writestr(name + "/spatial/tissue_positions_list.csv", coordinates)
                    archive.writestr(name + "/spatial/scalefactors_json.json", '{"tissue_lowres_scalef":1}')
                    archive.writestr(name + "/spatial/tissue_lowres_image.png", image.getvalue())
            payload = buffer.getvalue()
            accession = "S-BSST100"
            document = {"accno": accession, "section": {"attributes": [
                {"name": "Organism", "value": "Homo sapiens"}, {"name": "Organ", "value": "lung"},
                {"name": "Title", "value": "Visium lung"}],
                "files": [{"path": "spaceranger.zip", "size": len(payload)}]}}

            def api(_self, path, **params):
                if path == "/search":
                    return {"totalHits": 1, "hits": [{"accession": accession, "isPublic": True}]}
                if path.endswith("/info"):
                    return {"httpLink": "https://ftp.ebi.ac.uk/biostudies/fire/S-BSST/100/S-BSST100", "isPublic": True}
                return document

            def remote(*args, **kwargs):
                stream = io.BytesIO(payload)
                stream.bytes_read = len(payload)
                stream.reused_bytes = 0
                return stream

            with patch("sragent_spatial.biostudies.BioStudiesClient.json", api), patch(
                "sragent_spatial.biostudies.RangeZip", remote
            ):
                args = ("Homo sapiens", "lung", ["Visium"], root / "out")
                report = run_biostudies(*args, budget=1024**2, samples_per_study=3)
                self.assertEqual([r["status"] for r in report["samples"]],
                                 ["correspondence_passed", "missing_files", "mismatch"])
                passed = report["samples"][0]
                self.assertTrue(passed["checks"]["matrix_content"]["passed"])
                resumed = run_biostudies(*args, budget=1024**2, samples_per_study=3, resume=True)
                self.assertEqual(resumed["totals"], report["totals"])
                self.assertTrue(resumed["samples"][0]["resumed_after_local_sha256_check"])
                local = root / "out" / accession / "Visium" / passed["sample_id"].split("--")[1] / "filtered_feature_bc_matrix.h5"
                local.write_bytes(b"corrupted")
                repaired = run_biostudies(*args, budget=1024**2, samples_per_study=3, resume=True)
                self.assertEqual(repaired["samples"][0]["status"], "correspondence_passed")
                self.assertFalse(repaired["samples"][0]["files"]["h5"]["reused"])

                def two_studies(client, path, **params):
                    if path == "/search":
                        return {"totalHits": 2, "hits": [{"accession": item, "isPublic": True}
                                                       for item in ("S-BSST101", accession)]}
                    if "S-BSST101" in path:
                        if path.endswith("/info"):
                            return {"httpLink": "https://ftp.ebi.ac.uk/biostudies/fire/S-BSST/101/S-BSST101", "isPublic": True}
                        return {**document, "accno": "S-BSST101"}
                    return api(client, path, **params)

                def failing_remote(url, *args, **kwargs):
                    if "S-BSST101" in url:
                        raise OSError("simulated unavailable source")
                    return remote(url, *args, **kwargs)

                with patch("sragent_spatial.biostudies.BioStudiesClient.json", two_studies), patch(
                    "sragent_spatial.biostudies.RangeZip", failing_remote
                ):
                    continued = run_biostudies("Homo sapiens", "lung", ["Visium"], root / "failure", budget=1024**2)
                    self.assertEqual(continued["samples"][0]["status"], "access_failed")
                    self.assertEqual(continued["samples"][1]["status"], "correspondence_passed")
                with h5py.File(matrix, "r+") as f:
                    f["matrix/indptr"][:] = [0, 3, 2]
                with self.assertRaisesRegex(ValueError, "column pointers"):
                    hdf5_count_integrity(matrix)
