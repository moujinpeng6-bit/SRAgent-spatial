import io
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.tenx_cache import IMAGE, REQUIRED, RangeZip, prepare


class TenxCacheTests(unittest.TestCase):
    def test_prefetch_serves_small_reads_and_crosses_blocks_exactly(self):
        class Response(io.BytesIO):
            status = 206

            def __init__(self, payload, span):
                super().__init__(payload)
                self.headers = {"Content-Range": span, "ETag": '"v1"'}

        remote = RangeZip("https://ftp.ebi.ac.uk/example.zip", 8, block_size=4)
        with patch("sragent_spatial.tenx_cache.urlopen", side_effect=[
            Response(b"abcd", "bytes 0-3/8"), Response(b"efgh", "bytes 4-7/8")
        ]) as request:
            self.assertEqual(remote.read(1), b"a")
            self.assertEqual(remote.read(2), b"bc")
            self.assertEqual(remote.read(4), b"defg")
            self.assertEqual(remote.read(1), b"h")
            self.assertEqual(remote.read(1), b"")
            self.assertEqual(request.call_count, 2)
            self.assertEqual(remote.bytes_read, 8)

    def test_range_cache_replays_bytes_and_rejects_changed_archive_version(self):
        class Response(io.BytesIO):
            status = 206

            def __init__(self, data, span, etag):
                super().__init__(data)
                self.headers = {"Content-Range": span, "ETag": etag}

        with tempfile.TemporaryDirectory() as folder:
            url = "https://ftp.ebi.ac.uk/biostudies/fire/test/data.zip"
            with patch("sragent_spatial.tenx_cache.urlopen", return_value=Response(b"abcd", "bytes 0-3/8", '"v1"')):
                self.assertEqual(RangeZip(url, 8, cache=folder).read(4), b"abcd")
            replay = RangeZip(url, 8, cache=folder)
            with patch("sragent_spatial.tenx_cache.urlopen", side_effect=AssertionError("network")):
                self.assertEqual(replay.read(4), b"abcd")
                self.assertEqual(replay.bytes_read, 0)
                self.assertEqual(replay.reused_bytes, 4)
            with patch("sragent_spatial.tenx_cache.urlopen", return_value=Response(b"efgh", "bytes 4-7/8", '"v2"')):
                with self.assertRaises(OSError):
                    replay.read(4)

    def test_wrong_image_stops_preparation_and_verified_companions_are_reused(self):
        buffer = io.BytesIO()
        image_bytes = b"official image"
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("outs/" + IMAGE, image_bytes)
            for name in REQUIRED:
                archive.writestr("outs/" + name, name.encode())
        entry = {"url": "https://cf.10xgenomics.com/example.zip", "archive_bytes": len(buffer.getvalue()),
                 "member": "outs/" + IMAGE, "bytes": len(image_bytes), "crc32": f"{zlib.crc32(image_bytes):08x}"}

        def remote(*_):
            stream = io.BytesIO(buffer.getvalue())
            stream.bytes_read = 0
            return stream

        with tempfile.TemporaryDirectory() as folder, patch("sragent_spatial.tenx_cache.RangeZip", remote):
            root = Path(folder)
            image = root / IMAGE
            out = root / "companions"
            image.write_bytes(b"different img!")
            with self.assertRaisesRegex(ValueError, "does not match"):
                prepare(entry, image, out)
            self.assertFalse(out.exists())
            image.write_bytes(image_bytes)
            (root / REQUIRED[0]).write_bytes(REQUIRED[0].encode())
            first = prepare(entry, image, out)
            self.assertEqual(first["files"][REQUIRED[0]]["status"], "reused_local")
            self.assertEqual(first["files"][REQUIRED[1]]["status"], "downloaded")
            self.assertFalse((out / IMAGE).exists())
            second = prepare(entry, image, out)
            self.assertTrue(all(item["status"] == "reused_verified" for item in second["files"].values()))
