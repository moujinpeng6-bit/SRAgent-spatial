"""Evaluate shared sample cases without passing reference labels into predictors."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

from .screening import LABELS, screen_sample, validate_packet


def load_cases(path):
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("reference_status") != "assistant_prepared_pending_human_review":
        raise ValueError("Expected explicitly provisional reference status")
    seen = set()
    for case in dataset["cases"]:
        validate_packet(case["input"])
        acc = case["input"]["accession"]
        if acc in seen or case["reference"]["label"] not in LABELS:
            raise ValueError("Duplicate sample or invalid reference label")
        seen.add(acc)
    return dataset


def score(rows):
    completed = [r for r in rows if r["status"] == "completed"]
    correct = sum(r["prediction"]["label"] == r["reference_label"] for r in completed)
    confusion = Counter((r["reference_label"], r["prediction"]["label"]) for r in completed)
    return {"total_cases": len(rows), "completed": len(completed),
            "skipped": sum(r["status"].startswith("not_run") for r in rows),
            "failed": sum(r["status"] == "failed" for r in rows),
            "provisional_reference_agreement": correct / len(completed) if completed else None,
            "agreement_all_requested_cases": correct / len(rows) if completed else None,
            "confusion": [{"reference": a, "predicted": b, "count": n} for (a, b), n in confusion.items()]}


async def benchmark(dataset, methods, model=None, limit=None, timeout=120, predictor=None):
    if any(m not in {"rules", "original_agent", "spatial_agent"} for m in methods):
        raise ValueError("Unknown benchmark method")
    cases = dataset["cases"][:limit] if limit else dataset["cases"]
    rows = []
    for case in cases:
        packet = case["input"]
        for method in methods:
            row = {"accession": packet["accession"], "method": method,
                   "reference_label": case["reference"]["label"]}
            if method == "rules":
                prediction = screen_sample(packet)
                row.update(status="completed", prediction=prediction)
            elif not os.getenv("OPENAI_API_KEY") and predictor is None:
                row.update(status="not_run_missing_api_key")
            elif not model:
                row.update(status="not_run_missing_model_selection")
            else:
                try:
                    if predictor is None:
                        from .agents import predict_agent
                        prediction = await predict_agent(packet, method, model, timeout=timeout)
                    else:
                        prediction = await predictor(packet, method, model)
                    row.update(status="completed", prediction=prediction)
                except Exception as exc:
                    # Do not serialize exceptions that might contain credential/request details.
                    row.update(status="failed", failure_type=type(exc).__name__)
            rows.append(row)
    return {"reference_status": dataset["reference_status"], "case_count": len(cases),
            "method_scopes": {"original_agent": "Pinned upstream NCBI fetch Agent component, not the complete SRAgent hierarchy",
                              "spatial_agent": "Same upstream NCBI Agent plus upstream model factory and two sample-level tools"},
            "model": model, "results": rows,
            "summary": {method: score([r for r in rows if r["method"] == method]) for method in methods},
            "limitations": ["Development cases, selected nonrandomly; not independent gold labels or held-out generalization.",
                            "This run does not measure retrieval recall or validate spatial assets.",
                            "Same cases/model requested; extended agent has extra tools and may use more calls.",
                            "Original agent may query live metadata; this is not a strictly frozen-source comparison.",
                            "Per-case timeout and recursion limit do not guarantee a total billing cap; token cost not measured."]}
