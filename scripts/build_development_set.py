"""Package pre-reviewed decisions separately from predictors and remove unrelated clinical fields."""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sragent_spatial.geo import write_json
from sragent_spatial.screening import validate_packet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, default=Path("outputs/review_materials/samples.json"))
    parser.add_argument("--decisions", type=Path, default=Path("evaluation/reference_decisions.json"))
    parser.add_argument("--out", type=Path, default=Path("evaluation/lung_spatial_development.json"))
    args = parser.parse_args()
    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    cases = []
    for raw in json.loads(args.materials.read_text(encoding="utf-8")):
        accession = raw["accession"]
        label = decisions["labels"][accession]
        packet = {key: raw[key] for key in ("accession", "series", "source_url", "split")}
        fields = {}
        for key, values in raw["fields"].items():
            if key.startswith("Sample_characteristics"):
                values = [v for v in values if v.partition(":")[0].strip().lower()
                          in {"tissue", "tissue type", "organ", "organism part", "assay", "technology"}]
            if values:
                fields[key] = values
        packet["fields"] = fields
        packet["fields_sha256"] = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        validate_packet(packet)
        citations = [{"field": key, "quote": value, "source_url": packet["source_url"]}
                     for key, values in fields.items() for value in values
                     if key.startswith(("Sample_title", "Sample_source_name", "Sample_organism", "Sample_characteristics", "Sample_extract_protocol"))]
        cases.append({"input": packet, "reference": {"label": label,
                      "reason": decisions["reasons"][label], "evidence": citations},
                      "review_status": "assistant_prepared_pending_human_review"})
    if {c["input"]["accession"] for c in cases} != set(decisions["labels"]):
        raise ValueError("Materials and reviewed decisions must have exactly the same samples")
    dataset = {"dataset_id": "human_lung_spatial_development_v1", "reference_status": decisions["reference_status"],
               "description": decisions["description"], "coverage_gap": decisions["coverage_gap"],
               "label_counts": dict(Counter(c["reference"]["label"] for c in cases)), "cases": cases}
    write_json(args.out, dataset)
    with args.out.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["accession", "series", "source_url", "reference_label", "review_status"])
        writer.writeheader()
        for case in cases:
            p = case["input"]
            writer.writerow({"accession": p["accession"], "series": p["series"], "source_url": p["source_url"],
                             "reference_label": case["reference"]["label"], "review_status": case["review_status"]})
    print(json.dumps({"cases": len(cases), "label_counts": dataset["label_counts"]}))


if __name__ == "__main__":
    main()
