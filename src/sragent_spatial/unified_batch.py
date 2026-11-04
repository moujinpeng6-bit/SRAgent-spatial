"""One report for GEO candidate validation and registered 10x cache samples."""
from collections import Counter
from pathlib import Path

from .batch_validation import validate_candidates
from .screening import VISIUM_HD
from .tenx_batch import checkpoint, run_batch


def geo_platform(row):
    candidates = row.get("rule_prediction", {}).get("platform_candidates") or []
    fields = row.get("packet", {}).get("fields", {})
    if candidates == ["Visium"] and any(VISIUM_HD.search(value)
            for values in fields.values() for value in values):
        return "Visium HD"
    return candidates[0] if len(candidates) == 1 else "unclassified"


def files_ready(row):
    if row["status"] == "correspondence_passed" or row.get("preparation"):
        return True
    if row.get("bins_um"):
        return all(files_ready(item) for item in row["bins_um"].values())
    if row.get("slices"):
        return len(row["slices"]) == len(row["slice_candidates"]) and all(
            files_ready(item) for item in row["slices"].values())
    validation = row.get("validation") or row
    return validation.get("status") in {"mismatch", "validation_error"}


def counts(rows):
    attempted = [row for row in rows if row["selected"]]
    return {"found": sum(row["found"] for row in rows), "selected": len(attempted),
            "files_ready": sum(row["files_ready"] for row in attempted),
            "passed": sum(row["status"] == "correspondence_passed" for row in attempted),
            "failed": sum(row["status"] != "correspondence_passed" for row in attempted),
            "outcomes": dict(Counter(row["status"] for row in rows))}


def summarize(result):
    rows = result["samples"]
    result["totals"] = counts(rows)
    result["by_source"] = {source: counts([row for row in rows if row["source"] == source])
                           for source in sorted({row["source"] for row in rows})}
    result["by_platform"] = {platform: counts([row for row in rows if row["platform"] == platform])
                             for platform in sorted({row["platform"] for row in rows})}
    result["status"] = "completed_with_issues" if result["totals"]["failed"] else (
        "completed" if result["totals"]["selected"] else "no_candidates")


async def validate_sources(reports, geo_root, cache, sample_ids, out, *, download_bytes=0, probe_remote=False, resume=False):
    out = Path(out).resolve()
    for root in (geo_root, cache):
        if root is not None and (not Path(root).is_dir() or out.is_relative_to(Path(root).resolve())):
            raise ValueError("Input root must exist and output must be outside it")
    out.mkdir(parents=True, exist_ok=resume)
    path = out / "batch.json"
    result = {"status": "running", "samples": []}
    checkpoint(path, result)
    rows = result["samples"]
    # Keep the first eligible record if the same accession was screened more than once.
    selected = {}
    for index, report in enumerate(reports):
        for row in report["samples"]:
            sample = row["accession"]
            eligible = row.get("include_in_catalog") is True and row.get("status") == "completed"
            if sample not in selected or (eligible and not selected[sample][2]):
                selected[sample] = (index, row, eligible)
    for index, report in enumerate(reports):
        chosen = [row for group, row, eligible in selected.values() if group == index and eligible]
        if not chosen:
            continue
        options = {"download_bytes": download_bytes, "probe_remote": probe_remote}
        if resume:
            options["resume"] = True
        batch = await validate_candidates({**report, "samples": chosen}, geo_root, out / f"geo_{index + 1}", **options)
        rows.extend({"source": "GEO", "found": True, "selected": True, **row,
                     "platform": row.get("platform", "unclassified"), "files_ready": files_ready(row)}
                    for row in batch["samples"])
        checkpoint(path, result)
    rows.extend({"source": "GEO", "sample_id": sample, "platform": geo_platform(row),
                 "found": True, "selected": False, "files_ready": False,
                 "status": "not_selected", "screen_status": row.get("status"), "label": row.get("label")}
                for sample, (_, row, eligible) in selected.items() if not eligible)
    checkpoint(path, result)
    if sample_ids:
        batch = await run_batch(cache, sample_ids, out / "10x", None, timeout=600, local_tools=True, resume=resume and (out / "10x/batch.json").exists())
        rows.extend({"source": "10x_cache", "sample_id": sample, "platform": "Xenium", **row,
                     "selected": True, "found": row["status"] not in {"missing_source", "ambiguous_source", "sample_input_error"},
                     "files_ready": bool(row.get("preparation"))} for sample, row in batch["samples"].items())
    summarize(result)
    checkpoint(path, result)
    return result
