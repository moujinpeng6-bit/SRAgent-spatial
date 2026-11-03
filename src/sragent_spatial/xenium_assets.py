"""Resolve a sample-owned GEO archive into a validator manifest, without hardcoded paths."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .geo import checked_accession, write_json
from .xenium_validation import sha256_file

REQUIRED = {"experiment.xenium", "cells.csv.gz", "cell_feature_matrix.h5", "morphology_focus.ome.tif"}
OPTIONAL = {"cell_boundaries.csv.gz", "nucleus_boundaries.csv.gz"}


class AmbiguousBundle(ValueError):
    """More than one plausible local or published bundle."""


def archive_plan(accession, fields):
    checked_accession(accession)
    if not accession.startswith("GSM"):
        raise ValueError("A sample-owned archive requires a GSM")
    choices = []
    for key, values in fields.items():
        if not key.startswith("Sample_supplementary_file"):
            continue
        for url in values:
            parts = urlsplit(url)
            name = unquote(parts.path.rsplit("/", 1)[-1])
            if not name.endswith(".tar.gz"):
                continue
            if (parts.scheme not in {"ftp", "https"} or parts.hostname != "ftp.ncbi.nlm.nih.gov"
                    or parts.username or parts.password or parts.query or parts.fragment
                    or f"/{accession}/suppl/" not in parts.path or not name.startswith(accession + "_")
                    or any(c in name for c in '/\\<>:"|?*\r\n')):
                raise ValueError("Untrusted or cross-sample archive link")
            item = {"sample_id": accession, "filename": name, "source_url": url,
                    "download_url": urlunsplit(("https", parts.netloc, parts.path, "", "")), "field": key}
            if item not in choices:
                choices.append(item)
    if not choices:
        raise FileNotFoundError("No sample-owned tar.gz link")
    if len(choices) != 1:
        raise AmbiguousBundle("Expected exactly one sample archive; missing or ambiguous archives require review")
    return choices[0]


def find_archive(root, filename, max_files=100000):
    matches, count = [], 0
    for base, dirs, names in os.walk(Path(root).resolve(), followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
        for name in names:
            count += 1
            if count > max_files:
                raise ValueError("Local file inventory limit exceeded")
            if name == filename:
                path = Path(base) / name
                if path.is_file() and not path.is_symlink():
                    matches.append(path.resolve())
    if len(matches) > 1:
        raise AmbiguousBundle("Multiple exact local archives found; refusing arbitrary selection")
    return matches[0] if matches else None


class DownloadBudgetExceeded(ValueError):
    pass


def download_archive(plan, out, budget):
    if budget <= 0:
        raise ValueError("Archive not found locally and download byte budget is zero")
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    path = out / plan["filename"]
    if path.exists():
        raise ValueError("Refusing to reuse an unverified pre-existing download")
    partial = path.with_name(path.name + ".part")
    received = 0
    try:
        with urlopen(Request(plan["download_url"], headers={"User-Agent": "SRAgent-spatial/0.6"}), timeout=45) as response:
            if urlsplit(response.geturl()).hostname != "ftp.ncbi.nlm.nih.gov":
                raise ValueError("Unexpected archive download redirect")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > budget:
                raise DownloadBudgetExceeded("Archive exceeds download budget")
            with partial.open("xb") as stream:
                while chunk := response.read(min(1024*1024, budget-received+1)):
                    received += len(chunk)
                    if received > budget:
                        raise DownloadBudgetExceeded("Archive download byte budget exceeded")
                    stream.write(chunk)
            if length is not None and received != int(length):
                raise ValueError("Incomplete archive response")
        partial.replace(path)
    except Exception:
        # Preserve .part as an explicit incomplete artifact; never select it for validation.
        raise
    return path


def prepare_archive(plan, archive, out, sample_title):
    """Copy only bounded outs members into workspace; keep source archive read-only."""
    archive, out = Path(archive).resolve(), Path(out).resolve()
    if out == archive.parent or out.is_relative_to(archive.parent):
        raise ValueError("Prepared output must be outside the original archive directory")
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() or (out / (name + ".part")).exists() for name in REQUIRED | OPTIONAL):
        raise ValueError("Prepared outputs already exist; use a fresh run directory")
    before = archive.stat()
    records, prefixes, scanned, copied = {}, set(), 0, 0
    with tarfile.open(archive, "r|gz") as stream:
        for count, member in enumerate(stream, 1):
            scanned += member.size
            if count > 100000 or scanned > 16*1024**3:
                raise ValueError("Archive scan limit exceeded")
            p = PurePosixPath(member.name)
            if p.name not in REQUIRED | OPTIONAL or p.parent.name != "outs":
                continue
            if (p.is_absolute() or ".." in p.parts or "\\" in member.name or not member.isfile()
                    or not p.parts[0].startswith(plan["sample_id"] + "_") or p.name in records):
                raise ValueError("Unsafe, cross-sample or ambiguous archive member")
            limit = 256*1024**2 if p.name in {"morphology_focus.ome.tif", "cell_feature_matrix.h5"} else 64*1024**2
            if member.size > limit or copied + member.size > 512*1024**2:
                raise ValueError("Selected archive members exceed extraction budget")
            prefixes.add(str(p.parent))
            if len(prefixes) != 1:
                raise ValueError("Multiple output folders cannot be paired automatically")
            destination = out / p.name
            temporary = out / (p.name + ".part")
            digest, actual = hashlib.sha256(), 0
            with stream.extractfile(member) as source, temporary.open("xb") as target:
                while chunk := source.read(1024*1024):
                    actual += len(chunk); digest.update(chunk); target.write(chunk)
            if actual != member.size:
                raise ValueError("Truncated archive member")
            temporary.replace(destination)
            copied += actual
            records[p.name] = {"archive_member": member.name, "bytes": actual, "sha256": digest.hexdigest()}
    if REQUIRED - records.keys():
        raise FileNotFoundError("Missing required Xenium outputs: " + ",".join(sorted(REQUIRED-records.keys())))
    after = archive.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Archive changed while reading")
    experiment = json.loads((out / "experiment.xenium").read_text(encoding="utf-8-sig"))
    region = experiment.get("region_name")
    if not region or region.casefold() not in sample_title.casefold():
        raise ValueError("Archive experiment region cannot be matched to sample title")
    report = {"sample_id": plan["sample_id"], "source_url": plan["source_url"], "filename": plan["filename"],
              "archive_sha256": sha256_file(archive), "archive_bytes": before.st_size,
              "members": records, "copied_bytes": copied, "region_name": region,
              "sample_title": sample_title, "archive_unchanged": True,
              "remote_byte_identity_verified": False,
              "provenance_scope": "GEO sample-owned URL + exact local filename + sample archive root + experiment region; remote checksum not available"}
    write_json(out / "preparation.json", report)
    return {"sample_id": plan["sample_id"], "platform": "Xenium", "coordinate_unit": "um",
            "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=" + plan["sample_id"],
            "data_dir": str(out), "image": str(out / "morphology_focus.ome.tif"),
            "archive": str(archive), "archive_prefix": next(iter(prefixes))}, report
