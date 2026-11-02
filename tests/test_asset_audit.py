import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from sragent_spatial.asset_audit import LocalFileFetcher, audit_run, audit_sample, file_plan, matrix_dimensions


def sample_record(acc="GSM999001", suffix="section1"):
    names = [f"{acc}_{suffix}_{part}" for part in ["filtered_barcodes.tsv.gz", "filtered_features.tsv.gz",
             "filtered_matrix.mtx.gz", "tissue_positions_list.csv.gz", "scalefactors_json.json.gz", "tissue_lowres_image.png.gz"]]
    return {"accession": acc, "studies": ["GSE999001"], "source_url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}",
            "status": "completed", "include_in_catalog": True,
            "assets": [{"url": f"ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSM999nnn/{acc}/suppl/{name}"} for name in names]}


class Unavailable:
    def fetch(self, accession, asset):
        return {**asset, "status": "unavailable", "http_status": 403, "download_complete": False}


class LocalFiles:
    def __init__(self, root, *, matrix_columns=2):
        from PIL import Image
        self.root = Path(root)
        files = {"barcodes": b"a\nb\n", "features": b"gene1\tA\n gene2\tB\n",
                 "positions": b"a,1,0,0,10,20\nb,1,0,1,30,40\n",
                 "scalefactors": b'{"tissue_lowres_scalef":0.5}',
                 "matrix": f"%%MatrixMarket matrix coordinate integer general\n% fixture\n2 {matrix_columns} 0\n".encode()}
        for role, body in files.items():
            (self.root / (role + ".gz")).write_bytes(gzip.compress(body))
        image = self.root / "image.png"
        Image.new("RGB", (100, 100), "white").save(image)
        (self.root / "image.gz").write_bytes(gzip.compress(image.read_bytes()))

    def fetch(self, accession, asset):
        return {**asset, "status": "downloaded", "local_path": str(self.root / (asset["role"] + ".gz")),
                "http_status": 206 if asset["role"] == "matrix" else 200}


class AssetAuditTests(unittest.TestCase):
    def test_duplicate_local_filenames_are_not_chosen_arbitrarily(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for sub in ["a", "b"]:
                (root / sub).mkdir()
                (root / sub / "GSM999001_barcodes.tsv.gz").write_bytes(b"fixture")
            reader = LocalFileFetcher(root, {"GSM999001_barcodes.tsv.gz"})
            result = reader.fetch("GSM999001", {"filename": "GSM999001_barcodes.tsv.gz", "role": "barcodes"})
        self.assertEqual(result["status"], "ambiguous_local_file")

    def test_reordered_filtered_prefix_is_same_bundle(self):
        sample = sample_record()
        sample["assets"][0]["url"] = sample["assets"][0]["url"].replace("section1_filtered_barcodes", "filtered_section1_barcodes")
        plan = file_plan(sample)
        self.assertTrue(plan["metadata_bundle_consistent"])
        self.assertEqual(plan["bundle_anchors"], ["section1"])
        self.assertEqual(plan["missing_roles"], [])

    def test_other_sample_file_is_never_silently_paired(self):
        sample = sample_record()
        sample["assets"][0]["url"] = sample["assets"][0]["url"].replace("GSM999001_", "GSM999002_")
        row = audit_sample(sample, Unavailable())
        self.assertEqual(row["status"], "ambiguous_bundle")
        self.assertFalse(row["correspondence_verified"])

    def test_two_section_anchors_are_ambiguous(self):
        sample = sample_record()
        sample["assets"][-1]["url"] = sample["assets"][-1]["url"].replace("section1", "section2")
        self.assertEqual(audit_sample(sample, Unavailable())["status"], "ambiguous_bundle")

    def test_http_403_is_unavailable_not_mismatch_or_pass(self):
        row = audit_sample(sample_record(), Unavailable())
        self.assertTrue(row["plan"]["metadata_bundle_consistent"])
        self.assertEqual(row["status"], "unavailable")
        self.assertFalse(row["correspondence_verified"])
        self.assertEqual(row["checks"], {})
        self.assertEqual(row["issues"], [])

    def test_matrix_header_prefix_does_not_require_complete_gzip_stream(self):
        body = gzip.compress(b"%%MatrixMarket matrix coordinate integer general\n% comment\n20 7 0\n")
        self.assertEqual(matrix_dimensions(body[:-8])["observations"], 7)
        with self.assertRaises(ValueError):
            matrix_dimensions(gzip.compress(b"<html>error</html>"))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Install the validation extra")
    def test_actual_gzip_csv_png_and_matrix_dimensions_correspond(self):
        with tempfile.TemporaryDirectory() as folder:
            row = audit_sample(sample_record(), LocalFiles(folder))
        self.assertEqual(row["status"], "basic_correspondence_passed")
        self.assertTrue(row["correspondence_verified"])
        self.assertTrue(row["checks"]["image_decode"]["pixel_decode_verified"])
        self.assertEqual(row["checks"]["barcode_position_image"]["missing_expression_ids_count"], 0)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Install the validation extra")
    def test_matrix_shape_mismatch_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            row = audit_sample(sample_record(), LocalFiles(folder, matrix_columns=3))
        self.assertEqual(row["status"], "mismatch")
        self.assertFalse(row["correspondence_verified"])

    @unittest.skipUnless(importlib.util.find_spec("PIL") and importlib.util.find_spec("h5py"), "Install validation extra")
    def test_h5_barcode_order_is_checked_even_when_dimensions_match(self):
        import h5py
        for order, expected in [( [b"a", b"b"], "basic_correspondence_passed"), ([b"b", b"a"], "mismatch")]:
            with self.subTest(order=order), tempfile.TemporaryDirectory() as folder:
                reader = LocalFiles(folder)
                record = sample_record()
                record["assets"].append({"url": "ftp://ftp.ncbi.nlm.nih.gov/geo/samples/GSM999nnn/GSM999001/suppl/GSM999001_section1_filtered_feature_bc_matrix.h5"})
                with h5py.File(Path(folder) / "h5.gz", "w") as h5:
                    h5.create_dataset("matrix/barcodes", data=order)
                    h5.create_dataset("matrix/shape", data=[2, 2])
                row = audit_sample(record, reader)
                self.assertEqual(row["status"], expected)

    def test_all_selected_samples_have_unavailable_results_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "run.json").write_text(json.dumps({"samples": [sample_record(), sample_record("GSM999002")]}))
            report = audit_run(root / "run.json", root / "audit", fetcher=Unavailable())
        self.assertEqual(report["expected_samples"], 2)
        self.assertEqual(report["audited_samples"], 2)
        self.assertEqual(report["status_counts"], {"unavailable": 2})


if __name__ == "__main__":
    unittest.main()
