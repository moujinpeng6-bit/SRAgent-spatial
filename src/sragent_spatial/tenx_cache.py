"""Resolve a cached 10x Xenium image to its exact public ZIP companions."""
import io
import hashlib
import json
import re
import shutil
import tempfile
import time
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


class CacheInputError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def cache_entry(root, sample_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", sample_id):
        raise ValueError("Invalid cached sample ID")
    relative = f"samples/{sample_id}/{IMAGE}"
    rows = json.loads((root / "download_manifest.json").read_text(encoding="utf-8-sig"))
    matches = [row for row in rows if row.get("relative") == relative and row.get("type") == "zip"]
    if len(matches) != 1:
        raise CacheInputError("ambiguous_source" if matches else "missing_source",
                              "Expected one 10x ZIP entry for this cached image")
    entry = dict(matches[0])
    mirror = "https://s3-us-west-2.amazonaws.com/10x.files/"
    if entry["url"].startswith(mirror):
        entry["url"] = "https://cf.10xgenomics.com/" + entry["url"][len(mirror):]
    parsed = urlsplit(entry["url"])
    if (parsed.scheme != "https" or parsed.netloc != "cf.10xgenomics.com"
            or not re.fullmatch(r"/samples/xenium/[A-Za-z0-9._/-]+\.zip", parsed.path)
            or ".." in parsed.path.split("/") or parsed.query or parsed.fragment):
        raise ValueError("Expected an official 10x Xenium ZIP URL")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Cached image must stay inside the cache root")
    if not path.is_file():
        raise CacheInputError("missing_image", "Cached image is missing")
    return entry, path


class RangeZip(io.RawIOBase):
    def __init__(self, url, size, *, max_bytes=72 * MIB, cache=None, block_size=0, timeout=25):
        self.url = url
        self.size = size
        self.offset = 0
        self.bytes_read = 0
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.reused_bytes = 0
        self.etag = None
        if block_size < 0 or block_size > 4 * MIB:
            raise ValueError("ZIP prefetch block must be 0..4 MiB")
        self.block_size = block_size
        self._window_start, self._window = -1, b""
        self.cache = Path(cache) / hashlib.sha256(url.encode()).hexdigest() if cache else None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)

    def tell(self):
        return self.offset

    def seek(self, offset, whence=0):
        self.offset = (0, self.offset, self.size)[whence] + offset
        if self.offset < 0:
            raise ValueError("Negative ZIP offset")
        return self.offset

    def read(self, length=-1):
        if not self.block_size:
            return self._read_exact(length)
        length = min(max(0, self.size - self.offset), length if length >= 0 else self.size)
        if length > 4 * MIB:
            raise ValueError("ZIP read exceeds the transfer limit")
        chunks = []
        while length:
            start = self.offset // self.block_size * self.block_size
            if start != self._window_start:
                requested = self.offset
                self.offset = start
                try:
                    window = self._read_exact(min(self.block_size, self.size - start))
                finally:
                    self.offset = requested
                self._window_start, self._window = start, window
            begin = self.offset - start
            chunk = self._window[begin:begin + length]
            chunks.append(chunk)
            self.offset += len(chunk)
            length -= len(chunk)
        return b"".join(chunks)

    def _read_exact(self, length=-1):
        remaining = max(0, self.size - self.offset)
        length = remaining if length < 0 else min(remaining, length)
        if length > 4 * MIB:
            raise ValueError("ZIP range exceeds the transfer limit")
        if length == 0:
            return b""
        start, end = self.offset, self.offset + length - 1
        cached = self.cache / f"{start}-{end}.range" if self.cache else None
        if cached and cached.is_file():
            try:
                with cached.open("rb") as saved:
                    record = json.loads(saved.readline(4096))
                    data = saved.read(length + 1)
                if (record["size"] == self.size and record["etag"]
                        and (self.etag is None or record["etag"] == self.etag)
                        and len(data) == length and hashlib.sha256(data).hexdigest() == record["sha256"]):
                    self.etag = record["etag"]
                    self.reused_bytes += length
                    self.offset += length
                    return data
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if self.bytes_read + length > self.max_bytes:
            raise ValueError("ZIP range exceeds the transfer limit")
        mirror = self.url.replace("https://cf.10xgenomics.com/", "https://s3-us-west-2.amazonaws.com/10x.files/", 1)
        for base in dict.fromkeys((mirror, self.url)):
            for attempt in range(3):
                try:
                    headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
                    if self.etag:
                        headers["If-Match"] = self.etag
                    request = Request(f"{base}?xenium_range={start}_{end}", headers=headers)
                    with urlopen(request, timeout=self.timeout) as response:
                        if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{self.size}":
                            raise OSError("Expected an exact HTTP ZIP byte range")
                        data = response.read(length)
                        etag = response.headers.get("ETag", "")
                        if self.etag and etag != self.etag:
                            raise OSError("ZIP object version changed")
                    if len(data) != length:
                        raise OSError("ZIP range was truncated")
                    if etag.startswith('"') and etag.endswith('"'):
                        self.etag = etag
                        if cached:
                            with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as saved:
                                saved.write(json.dumps({"size": self.size, "etag": etag,
                                    "sha256": hashlib.sha256(data).hexdigest()}).encode() + b"\n")
                                saved.write(data)
                                temporary = Path(saved.name)
                            temporary.replace(cached)
                    self.bytes_read += length
                    self.offset += length
                    return data
                except (URLError, OSError) as exc:
                    last_error = exc
                    if (getattr(exc, "code", None) in {401, 403, 404, 412, 429}
                            or str(exc) == "ZIP object version changed"):
                        break
                    if attempt < 2:
                        time.sleep(0.25 * (attempt + 1))
        raise OSError(f"Public ZIP range unavailable: {type(last_error).__name__}: {last_error}") from last_error


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
            raise CacheInputError("image_mismatch", "Cached image does not match the official ZIP entry")
        prefix = image_name[:-len(IMAGE)]
        selected = []
        for name in REQUIRED:
            items = [item for item in archive.infolist() if item.filename == prefix + name]
            if len(items) != 1 or items[0].is_dir():
                raise CacheInputError("ambiguous_source" if items else "missing_companion",
                                      f"Expected one same-directory ZIP member for {name}")
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
