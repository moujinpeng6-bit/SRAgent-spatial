import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.tenx_online import ArchiveStream, CatalogAccessError, collect, platform_for, read_catalog_snapshot


class TenxOnlineTests(unittest.TestCase):
    def test_snapshot_filters_by_metadata_and_rejects_nonofficial_archives(self):
        row = {"species": "Human", "tissue": "Tonsil", "platform": "Visium HD",
               "sample_id": "tonsil", "source_url": "https://www.10xgenomics.com/datasets/tonsil",
               "archives": {"binned_outputs": [
                   "https://cf.10xgenomics.com/samples/spatial-exp/4.1.0/Example/Example_binned_outputs.tar.gz"]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.json"
            path.write_text(json.dumps({"samples": [row, row]}), encoding="utf-8")
            self.assertEqual(read_catalog_snapshot(path, "Homo sapiens", "tonsil", ["Visium HD"], 2), [row])
            self.assertEqual(read_catalog_snapshot(path, "Homo sapiens", "lung", ["Visium HD"], 2), [])
            row["archives"]["binned_outputs"] = ["https://example.org/binned_outputs.tar.gz"]
            path.write_text(json.dumps({"samples": [row]}), encoding="utf-8")
            with self.assertRaises(CatalogAccessError):
                read_catalog_snapshot(path, "Homo sapiens", "tonsil", ["Visium HD"], 2)

    def test_resume_reuses_verified_range_and_requests_only_remaining_bytes(self):
        class RangeResponse(io.BytesIO):
            status = 206

            def __init__(self, data, span):
                super().__init__(data)
                self.headers = {"Content-Range": f"bytes {span}/6", "ETag": '"version"'}

        url = "https://cf.10xgenomics.com/test.tar.gz"
        with tempfile.TemporaryDirectory() as folder, patch(
            "sragent_spatial.tenx_online.RANGE_BYTES", 4
        ), patch("sragent_spatial.tenx_online.urlopen", side_effect=[
            RangeResponse(b"abcd", "0-3"), RangeResponse(b"ef", "4-5"),
        ]) as get:
            cache = Path(folder)
            self.assertEqual(ArchiveStream(url, 6, cache).read(4), b"abcd")
            resumed = ArchiveStream(url, 6, cache)
            self.assertEqual(resumed.read(6), b"abcdef")
            self.assertEqual((resumed.reused, resumed.downloaded), (4, 2))
            self.assertEqual([call.args[0].get_header("Range") for call in get.call_args_list],
                             ["bytes=0-3", "bytes=4-5"])
            self.assertEqual(get.call_args.args[0].get_header("If-match"), '"version"')

    def test_selects_filtered_2um_members_and_bounds_matrix_prefix(self):
        stream = io.BytesIO()
        prefix = "binned_outputs/square_002um/"
        payloads = {
            "raw_feature_bc_matrix/barcodes.tsv.gz": b"raw",
            "filtered_feature_bc_matrix/matrix.mtx.gz": b"x" * 70000,
            "filtered_feature_bc_matrix/barcodes.tsv.gz": b"filtered",
            "spatial/tissue_positions.parquet": b"positions",
            "spatial/scalefactors_json.json": b"scale",
            "spatial/tissue_lowres_image.png": b"image",
        }
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for name, data in payloads.items():
                entry = tarfile.TarInfo(prefix + name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        stream.seek(0)
        with tempfile.TemporaryDirectory() as folder:
            files = collect(stream, Path(folder))
            self.assertEqual(files["barcodes"]["path"].read_bytes(), b"filtered")
            self.assertEqual(files["matrix"]["path"].stat().st_size, 65536)
            self.assertEqual(set(files), {"matrix", "barcodes", "positions", "scalefactors", "image"})
        self.assertEqual(platform_for("HD Spatial Gene Expression v1", "Human Kidney"), "Visium HD")
