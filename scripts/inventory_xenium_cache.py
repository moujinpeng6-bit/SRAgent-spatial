"""Inspect an existing Xenium download manifest without downloading payloads."""
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def inspect(root, tissue="*"):
    root = root.resolve()
    catalog = json.loads((root / "sample_metadata.json").read_text(encoding="utf-8-sig"))
    manifest = json.loads((root / "download_manifest.json").read_text(encoding="utf-8-sig"))
    by_row = {}
    for asset in manifest:
        by_row.setdefault(asset.get("row"), []).append(asset)
    rows = []
    for sample in catalog["samples"]:
        if tissue != "*" and sample.get("organ", "").casefold() != tissue.casefold():
            continue
        files = []
        for asset in by_row.get(sample.get("row"), []):
            relative = Path(asset["relative"])
            if relative.is_absolute() or ".." in relative.parts:
                continue
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                continue
            source = urlsplit(asset["url"])
            if source.scheme != "https" or source.netloc not in {"cf.10xgenomics.com", "ftp.ncbi.nlm.nih.gov"}:
                continue
            expected = asset.get("bytes")
            present = path.is_file()
            size = path.stat().st_size if present else None
            state = "missing_local_file"
            if present:
                state = ("local_present_size_unverified" if expected is None else
                         "local_exact_listed_size" if size == expected else "local_size_mismatch")
            image_header = None
            if present and path.suffix.lower() in {".tif", ".tiff"}:
                with path.open("rb") as stream:
                    image_header = stream.read(4) in {b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"}
            files.append({"name": relative.name, "url": asset["url"], "archive_member": asset.get("member"),
                          "kind": asset.get("kind"), "state": state, "bytes": size, "expected_bytes": expected,
                          "tiff_signature_valid": image_header})
        rows.append({"slide": sample.get("slide"), "organ": sample.get("organ"), "source_url": sample.get("url"),
                     "files": files})
    return {"scope": "manifest-declared local assets only; no download or spatial correspondence implied",
            "remote_payload_bytes": 0, "organ_counts": dict(Counter(row["organ"] for row in rows)),
            "asset_status_counts": dict(Counter(file["state"] for row in rows for file in row["files"])),
            "samples": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tissue", default="*")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.root.resolve()):
        parser.error("output must be outside the source root")
    report = inspect(args.root, args.tissue)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
