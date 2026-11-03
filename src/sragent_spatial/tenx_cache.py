"""Resolve a cached 10x Xenium image to its exact public ZIP companions."""
import io
import json
import re
import shutil
import tempfile
import zipfile
import zlib
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .geo import write_json

REQUIRED = ("experiment.xenium", "cells.csv.gz", "cell_feature_matrix.h5")
IMAGE = "morphology_focus.ome.tif"
MIB = 1024**2


def cache_entry(root, sample_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", sample_id):
        raise ValueError("Invalid cached sample ID")
    relative = f"samples/{sample_id}/{IMAGE}"
    rows = json.loads((root / "download_manifest.json").read_text(encoding="utf-8-sig"))
    matches = [row for row in rows if row.get("relative") == relative and row.get("type") == "zip"]
    if len(matches) != 1:
        raise ValueError("Expected one 10x ZIP entry for this cached image")
    entry = matches[0]
    parsed = urlsplit(entry["url"])
    if (parsed.scheme != "https" or parsed.netloc != "cf.10xgenomics.com"
            or not re.fullmatch(r"/samples/xenium/[A-Za-z0-9._/-]+\.zip", parsed.path)
            or ".." in parsed.path.split("/") or parsed.query or parsed.fragment):
        raise ValueError("Expected an official 10x Xenium ZIP URL")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Cached image must exist inside the cache root")
    return entry, path


class RangeZip(io.RawIOBase):
    def __init__(self, url, size):
        self.url = url
        self.size = size
        self.offset = 0
        self.bytes_read = 0

    def tell(self):
        return self.offset

    def seek(self, offset, whence=0):
        self.offset = (0, self.offset, self.size)[whence] + offset
        if self.offset < 0:
            raise ValueError("Negative ZIP offset")
        return self.offset

    def read(self, length=-1):
        remaining = max(0, self.size - self.offset)
        length = remaining if length < 0 else min(remaining, length)
        if length > 4 * MIB or self.bytes_read + length > 72 * MIB:
            raise ValueError("ZIP range exceeds the transfer limit")
        if length == 0:
            return b""
        start, end = self.offset, self.offset + length - 1
        mirror = self.url.replace("https://cf.10xgenomics.com/", "https://s3-us-west-2.amazonaws.com/10x.files/", 1)
        for base in (mirror, self.url):
            try:
                request = Request(f"{base}?xenium_range={start}_{end}",
                                  headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
                with urlopen(request, timeout=25) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}":
                        raise OSError("Expected an exact HTTP ZIP byte range")
                    data = response.read(length)
                if len(data) != length:
                    raise OSError("ZIP range was truncated")
                self.bytes_read += length
                self.offset += length
                return data
            except (URLError, OSError) as exc:
                last_error = exc
        raise OSError("10x ZIP range unavailable") from last_error


def verify_member(path, info):
    if not path.is_file() or path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open("rb") as stream:
        while chunk := stream.read(MIB):
            crc = zlib.crc32(chunk, crc)
    return crc == info.CRC


def prepare(entry, image, destination):
    remote = RangeZip(entry["url"], entry["archive_bytes"])
    with zipfile.ZipFile(remote) as archive:
        image_name = entry["member"]
        images = [item for item in archive.infolist() if item.filename == image_name]
        if len(images) != 1 or image_name.rsplit("/", 1)[-1] != IMAGE:
            raise ValueError("Ambiguous cached image ZIP member")
        image_info = images[0]
        if (image_info.file_size != entry["bytes"] or f"{image_info.CRC:08x}" != entry["crc32"].lower()
                or not verify_member(image, image_info)):
            raise ValueError("Cached image does not match the official ZIP entry")
        prefix = image_name[:-len(IMAGE)]
        selected = []
        for name in REQUIRED:
            items = [item for item in archive.infolist() if item.filename == prefix + name]
            if len(items) != 1 or items[0].is_dir():
                raise ValueError(f"Expected one same-directory ZIP member for {name}")
            selected.append(items[0])
        if any(not 0 < item.file_size <= 64 * MIB for item in selected) or sum(
                item.compress_size for item in selected) > 64 * MIB:
            raise ValueError("Companion files exceed the transfer limit")
        destination.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, info in zip(REQUIRED, selected):
            path = destination / name
            if verify_member(path, info):
                status = "reused_verified"
            elif verify_member(image.parent / name, info):
                status = "reused_local"
            else:
                status = "downloaded"
            if status != "reused_verified":
                with tempfile.TemporaryDirectory(dir=destination) as temporary:
                    staged = Path(temporary) / name
                    if status == "reused_local":
                        shutil.copyfile(image.parent / name, staged)
                    else:
                        with archive.open(info) as src, staged.open("wb") as dst:
                            shutil.copyfileobj(src, dst, MIB)
                    if not verify_member(staged, info):
                        raise ValueError(f"CRC mismatch for {name}")
                    staged.replace(path)
            files[name] = {"status": status, "bytes": info.file_size, "crc32": f"{info.CRC:08x}"}
    result = {"status": "prepared", "archive_url": entry["url"], "archive_bytes": entry["archive_bytes"],
              "image": {"status": "reused_verified", "bytes": image_info.file_size, "crc32": f"{image_info.CRC:08x}"},
              "files": files, "zip_range_bytes": remote.bytes_read}
    write_json(destination / "provenance.json", result)
    return result
