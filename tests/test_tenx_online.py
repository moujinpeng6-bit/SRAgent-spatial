import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.tenx_online import collect, platform_for


class TenxOnlineTests(unittest.TestCase):
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
        stream.offset = len(stream.getvalue())
        with tempfile.TemporaryDirectory() as folder, patch(
            "sragent_spatial.tenx_online.ArchiveStream", return_value=stream
        ):
            files, _ = collect("https://example.invalid/test.tar.gz", Path(folder), 100000)
            self.assertEqual(files["barcodes"]["path"].read_bytes(), b"filtered")
            self.assertEqual(files["matrix"]["path"].stat().st_size, 65536)
            self.assertEqual(set(files), {"matrix", "barcodes", "positions", "scalefactors", "image"})
        self.assertEqual(platform_for("HD Spatial Gene Expression v1", "Human Kidney"), "Visium HD")
