"""Check four native Visium HD attachments from one locally staged source bundle."""
import argparse
import hashlib
import json
from pathlib import Path

from sragent_spatial.asset_audit import decode_image
from sragent_spatial.spatial import validate_visium

NAMES = {
    "barcodes": "barcodes.tsv.gz",
    "positions": "tissue_positions.parquet",
    "scalefactors": "scalefactors_json.json",
    "image": "tissue_lowres_image.png",
}


def verify(root, spec):
    if set(spec["assets"]) != set(NAMES):
        raise ValueError("Manifest must name exactly the four correspondence assets")
    paths, evidence = {}, {}
    for role, name in NAMES.items():
        path = root / name
        declared = spec["assets"][role]
        with path.open("rb") as stream:
            md5 = hashlib.file_digest(stream, "md5").hexdigest()
        if path.stat().st_size != declared["bytes"] or md5 != declared["md5"]:
            raise ValueError(f"Size or publisher MD5 mismatch: {name}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        paths[role] = path
        evidence[role] = dict(declared, filename=name, sha256=digest)
    size = spec["bin_um"]
    checks = validate_visium(paths["barcodes"], paths["positions"], paths["scalefactors"], paths["image"], bin_um=size)
    return {"status": "correspondence_passed" if not checks["issues"] else "mismatch",
            "source": spec["source"], "published_directory": spec["published_directory"],
            "bin_um": size, "assets": evidence, "checks": checks, "image_decode": decode_image(paths["image"]),
            "scope": "published filtered expression barcode list, coordinates, scale and image; matrix not read"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.root, json.loads(args.manifest.read_text(encoding="utf-8-sig")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(report["status"])
    return 0 if report["status"] == "correspondence_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
