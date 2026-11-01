"""Read-only local inventory and source-link comparisons. No extraction/upload."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .geo import asset_kind


def is_link(path: Path) -> bool:
    # Includes Windows directory junctions on Python 3.11.
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def inspect_local(root: Path, max_files=5000) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Local root must be a directory")
    sources = {}
    manifest_error = None
    manifest = root / "download_manifest.json"
    if manifest.is_file() and manifest.stat().st_size < 10 * 1024 * 1024:
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(entries, list):
                raise ValueError("Expected a list")
            for item in entries:
                if isinstance(item, dict) and isinstance(item.get("relative"), str):
                    relative = item["relative"].replace("\\", "/")
                    sources[relative] = item.get("url")
        except (ValueError, OSError) as exc:
            manifest_error = str(exc)
    files = []
    errors = []
    truncated = False
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not is_link(Path(folder) / d))
        for name in sorted(names):
            path = Path(folder) / name
            if is_link(path):
                continue
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    continue
                if len(files) >= max_files:
                    truncated = True
                    break
                relative = resolved.relative_to(root).as_posix()
                size = resolved.stat().st_size
                incomplete = name.endswith((".part", ".partial", ".tmp", ".crdownload")) or size == 0
                files.append({"relative_path": relative, "bytes": size,
                              "kind_guess": asset_kind(resolved.as_uri()), "incomplete": incomplete,
                              "accessions_in_name": sorted(set(re.findall(r"(?:GSE|GSM)\d+", name))),
                              "source_url_from_manifest": sources.get(relative) or sources.get(relative.removesuffix(".part"))})
            except OSError as exc:
                errors.append({"relative_path": str(path.relative_to(root)), "error": str(exc)})
        if truncated:
            break
    return {"root": str(root), "files": files, "file_count": len(files), "truncated": truncated,
            "errors": errors, "manifest_error": manifest_error,
            "kind_counts": dict(Counter(x["kind_guess"] for x in files)),
            "incomplete_count": sum(x["incomplete"] for x in files),
            "note": "Only names, sizes and optional download manifest read; no checksums, matrix IDs, image alignment or archive contents verified."}


def normalized_url(value: str) -> str:
    parts = urlsplit(value)
    # NCBI FTP/HTTPS may refer to the same published asset; queries remain significant.
    scheme = "https" if parts.hostname == "ftp.ncbi.nlm.nih.gov" and parts.scheme == "ftp" else parts.scheme
    return parts._replace(scheme=scheme, path=unquote(parts.path)).geturl()


def compare_sources(records: list[dict], inventory: dict) -> dict:
    comparisons = []
    for record in records:
        for asset in record["assets"]:
            published = normalized_url(asset["url"])
            name = unquote(urlsplit(asset["url"]).path.rsplit("/", 1)[-1])
            matches = []
            for local in inventory["files"]:
                provenance = local.get("source_url_from_manifest")
                if provenance and normalized_url(provenance) == published:
                    basis = "source_url_match"
                elif Path(local["relative_path"]).name == name:
                    basis = "filename_only"
                else:
                    continue
                matches.append({"relative_path": local["relative_path"], "basis": basis,
                                "incomplete": local["incomplete"]})
            comparisons.append({"record_id": record["record_id"], "asset_url": asset["url"], "matches": matches})
    return {"assets": comparisons, "assets_with_local_match": sum(bool(x["matches"]) for x in comparisons),
            "note": "Links/names only. Partial files are incomplete; source matching never proves checksums, physical section identity or spatial registration."}
