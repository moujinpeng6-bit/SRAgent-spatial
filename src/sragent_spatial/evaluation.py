"""Evaluate shared sample cases without passing reference labels into predictors."""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter

from .screening import LABELS, parse_prediction, screen_sample, validate_packet


def failure_details(exc):
    """Expose only allowlisted diagnostics, never provider messages or request bodies."""
    details = {"failure_type": type(exc).__name__}
    if isinstance(exc, json.JSONDecodeError):
        details["failure_stage"] = "model_json"
    elif isinstance(exc, ValueError) and str(exc) in {
        "Model returned the wrong sample identity",
        "Model label or reason is invalid",
        "Non-unknown model labels require sample citations",
        "Model evidence must cite this exact sample",
        "Model quote not found in the supplied sample field",
        "Agent returned no final message",
        "Agent did not return text JSON",
    }:
        details["failure_stage"] = "model_output_validation"
        details["failure_reason"] = str(exc)
    status = getattr(exc, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        details["http_status"] = status
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in {
        "invalid_api_key", "insufficient_quota", "model_not_found",
        "rate_limit_exceeded", "invalid_organization", "organization_deactivated",
        "unsupported_parameter", "unsupported_value",
    }:
        details["provider_error_code"] = code
    if details["failure_type"] == "AuthenticationError" or status == 401:
        details["failure_hint"] = "Authentication rejected. Re-enter a valid provider API key locally (OpenAI: --prompt-key); check endpoint, model id and account billing."
    elif details["failure_type"] == "PermissionDeniedError" or status == 403:
        details["failure_hint"] = "Check project/model permissions and API access policies."
    elif code == "insufficient_quota":
        details["failure_hint"] = "Check this API project's available quota and billing."
    return details


def load_cases(path):
    dataset = json.loads(path.read_text(encoding="utf-8"))
    status = dataset.get("reference_status")
    if status not in {"assistant_prepared_pending_human_review", "researcher_reviewed"}:
        raise ValueError("Reference status must be explicit")
    seen = set()
    for case in dataset["cases"]:
        validate_packet(case["input"])
        acc = case["input"]["accession"]
        if acc in seen or case["reference"]["label"] not in LABELS:
            raise ValueError("Duplicate sample or invalid reference label")
        if status == "researcher_reviewed":
            reference = case["reference"]
            if not all(isinstance(reference.get(key), str) and reference[key].strip()
                       for key in ("reviewer", "reviewed_on", "reason")) or not reference.get("evidence"):
                raise ValueError("Reviewed references require reviewer, date, reason and sample evidence")
            parse_prediction(json.dumps({**reference, "accession": acc}), case["input"])
        seen.add(acc)
    return dataset


def score(rows):
    completed = [r for r in rows if r["status"] == "completed"]
    correct = sum(r["prediction"]["label"] == r["reference_label"] for r in completed)
    confusion = Counter((r["reference_label"], r["prediction"]["label"]) for r in completed)
    predicted_eligible = sum(r["prediction"]["label"] == "eligible" for r in completed)
    reference_eligible = sum(r["reference_label"] == "eligible" for r in rows)
    true_eligible = confusion["eligible", "eligible"]
    return {"total_cases": len(rows), "completed": len(completed),
            "skipped": sum(r["status"].startswith("not_run") for r in rows),
            "failed": sum(r["status"] == "failed" for r in rows),
            "provisional_reference_agreement": correct / len(completed) if completed else None,
            "agreement_all_requested_cases": correct / len(rows) if completed else None,
            "eligible": {"correct_accepts": true_eligible, "false_accepts": predicted_eligible - true_eligible,
                         "reference_eligible": reference_eligible,
                         "precision": true_eligible / predicted_eligible if predicted_eligible else None,
                         "recall_all_requested": true_eligible / reference_eligible
                         if completed and reference_eligible else None},
            "confusion": [{"reference": a, "predicted": b, "count": n} for (a, b), n in confusion.items()]}


async def benchmark(dataset, methods, model=None, limit=None, timeout=120, predictor=None):
    if any(m not in {"rules", "original_agent", "spatial_agent", "sragent"} for m in methods):
        raise ValueError("Unknown benchmark method")
    from .agents import credentials_present, provider
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
            elif not credentials_present() and predictor is None:
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
                    row.update(status="failed", **failure_details(exc))
            rows.append(row)
    return {"reference_status": dataset["reference_status"], "case_count": len(cases),
            "method_scopes": {"original_agent": "Pinned upstream NCBI fetch Agent component, not the complete SRAgent hierarchy",
                              "spatial_agent": "Same upstream NCBI Agent plus upstream model factory and two sample-level tools",
                              "sragent": "Complete pinned upstream SRAgent supervisor (Entrez, ncbi_fetch, BigQuery, sequences) plus the same two sample-level tools"},
            "provider": provider(), "model": model, "results": rows,
            "summary": {method: score([r for r in rows if r["method"] == method]) for method in methods},
            "limitations": [("Researcher-reviewed sample labels; sampling and generalization depend on the supplied cases."
                             if dataset["reference_status"] == "researcher_reviewed" else
                             "Provisional labels awaiting independent researcher review; no generalization claim."),
                            "This run does not measure retrieval recall or validate spatial assets.",
                            "Same cases/model requested; extended agent has extra tools and may use more calls.",
                            "Original agent may query live metadata; this is not a strictly frozen-source comparison.",
                            "Per-case timeout and recursion limit do not guarantee a total billing cap; token cost not measured."]}
