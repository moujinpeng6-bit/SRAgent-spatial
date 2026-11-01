"""Explicitly fetch only four small public Visium companion files; never the matrix or full image."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sragent_spatial.geo import MetadataClient, MAX_RESPONSE, checked_accession, lookup, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("accession")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("outputs/cache"))
    args = parser.parse_args()
    acc = checked_accession(args.accession)
    if not acc.startswith("GSM"):
        raise ValueError("Use a GSM sample")
    client = MetadataClient(args.cache)
    records, _ = lookup(client, acc)
    suffixes = ("_barcodes.tsv.gz", "_tissue_positions.csv.gz", "_scalefactors_json.json.gz", "_tissue_lowres_image.png.gz")
    manifest = []
    args.out.mkdir(parents=True, exist_ok=True)
    for asset in records[0]["assets"]:
        parts = urlsplit(asset["url"])
        filename = parts.path.rsplit("/", 1)[-1]
        if parts.hostname != "ftp.ncbi.nlm.nih.gov" or not filename.startswith(acc + "_") or not filename.endswith(suffixes):
            continue
        url = urlunsplit(("https", parts.netloc, parts.path, "", ""))
        body, transport = client._download(url)
        if not body or len(body) > MAX_RESPONSE:
            raise ValueError("Companion file exceeds 8 MiB limit")
        path = args.out / filename
        path.write_bytes(body)
        manifest.append({"accession": acc, "source_url": url, "relative": filename,
                         "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "transport": transport})
    write_json(args.out / "source_manifest.json", manifest)
    print(json.dumps({"files": len(manifest), "total_bytes": sum(x["bytes"] for x in manifest)}))


if __name__ == "__main__":
    main()
