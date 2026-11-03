import io
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.tenx_cache import IMAGE, REQUIRED, prepare


class TenxCacheTests(unittest.TestCase):
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
