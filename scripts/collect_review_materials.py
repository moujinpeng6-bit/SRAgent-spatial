"""Collect public sample-only fields, without generating reference labels."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sragent_spatial.geo import MetadataClient, lookup, parse_soft, source_url, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=Path("configs/review_sources.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("outputs/network_check_20261101/cache"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    client = MetadataClient(args.cache, offline=args.offline)
    samples = []
    seen = set()
    for selection in json.loads(args.sources.read_text(encoding="utf-8"))["sources"]:
        records, scope = lookup(client, selection["accession"], selection["max_samples"])
        for record in records[1:]:
            acc = record["source_accession"]
            if acc in seen:
                continue
            seen.add(acc)
            raw = parse_soft(client.soft(acc), acc)
            selected = {k: v for k, v in raw.items() if k.startswith((
                "Sample_title", "Sample_source_name", "Sample_organism", "Sample_characteristics",
                "Sample_description", "Sample_extract_protocol", "Sample_library_", "Sample_data_processing"
            ))}
            evidence_text = json.dumps(selected, sort_keys=True, ensure_ascii=False)
            samples.append({"accession": acc, "series": selection["accession"],
                            "source_url": source_url(acc), "fields": selected,
                            "fields_sha256": hashlib.sha256(evidence_text.encode()).hexdigest(),
                            "split": "development"})
        print(selection["accession"], "collected", len(records) - 1, flush=True)
    write_json(args.out, samples)
    write_json(args.out.with_suffix(".run.json"), {
        "collected_at": datetime.now(timezone.utc).isoformat(), "sample_count": len(samples),
        "selection": json.loads(args.sources.read_text(encoding="utf-8")), "requests": client.requests,
        "reference_status": "unlabelled_public_metadata_not_independent_gold_standard"
    })


if __name__ == "__main__":
    main()
