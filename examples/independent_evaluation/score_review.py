"""Rescore saved, complete benchmark observations with independently submitted labels."""
import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from run import ratio


def score(summary, review):
    if review.get("independent_of_development") is not True or not review.get("reviewer", "").strip():
        raise ValueError("Independent reviewer attestation and reviewer ID are required")
    reviewed_on = date.fromisoformat(review["reviewed_on"]).isoformat()
    observations = {(q["query"], r["sample"]): r["selected"] for q in summary["queries"]
                    if q["workflow_complete"] for r in q["evaluation"]["reference_samples"]}
    submitted = {}
    for row in review["rows"]:
        key = row["query"], row["sample"]
        url = urlsplit(row.get("source_url", ""))
        if (key in submitted or row["decision"] not in {"include","exclude","uncertain"}
                or row.get("health") not in {"healthy","diseased","adjacent","mixed","unknown"}
                or not row.get("evidence", "").strip() or url.scheme != "https" or not url.netloc):
            raise ValueError("Invalid or duplicate review row: " + str(key))
        submitted[key] = row
    if not observations or set(submitted) != set(observations) or any(not q["workflow_complete"] for q in summary["queries"]):
        raise ValueError("All samples in the completed benchmark require exactly one review row")
    counts = {"true_positive":0, "false_positive":0, "true_negative":0, "false_negative":0, "uncertain":0}
    samples = []
    for key, row in submitted.items():
        chosen = observations[key]
        decision = row["decision"]
        if decision == "uncertain":
            outcome = "uncertain"
        elif decision == "include":
            outcome = "true_positive" if chosen else "false_negative"
        else:
            outcome = "false_positive" if chosen else "true_negative"
        counts[outcome] += 1
        samples.append({**row,"machine_selected":chosen,"classification":outcome})
    tp, fp, fn = (counts[name] for name in ("true_positive","false_positive","false_negative"))
    return {"reviewer":review["reviewer"],"reviewed_on":reviewed_on,
            "reference_sha256":summary["reference_sha256"],"counts":counts,"samples":samples,
            "recall":ratio(tp,tp+fn),"precision_among_reviewed_selected":ratio(tp,tp+fp)}


def main():
    parser = argparse.ArgumentParser()
    for arg in ("summary","review","out"):
        parser.add_argument("--"+arg, type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8-sig"))
    review = json.loads(args.review.read_text(encoding="utf-8-sig"))
    result = score(summary,review)
    with args.out.open("x", encoding="utf8") as stream:
        json.dump(result,stream,indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
