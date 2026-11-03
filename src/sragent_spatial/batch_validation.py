"""Prepare and validate eligible Xenium and Visium samples from a discovery report."""
import asyncio
import re
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError

from .asset_audit import FileFetcher, LocalFileFetcher, audit_sample, file_plan
from .geo import write_json
from .pipeline import PipelineSession
from .xenium_assets import DownloadBudgetExceeded


def audit_visium_candidate(row, root, out, download_bytes):
    plan = file_plan(row)
    local = LocalFileFetcher(root, {asset["filename"] for asset in plan["roles"].values()})
    remote = FileFetcher(out / "files", download_bytes=download_bytes)

    class Fetcher:
        def fetch(self, accession, asset):
            result = local.fetch(accession, asset)
            if result["status"] == "missing_local_file" and download_bytes:
                return remote.fetch(accession, asset)
            return result

    audit = audit_sample(row, Fetcher())
    write_json(out / "validation.json", audit)
    status = audit["status"]
    if status == "basic_correspondence_passed":
        status = "correspondence_passed"
    elif status == "unavailable":
        states = {audit["files"][role]["status"] for role in audit["unavailable_required_roles"]}
        if states <= {"missing_local_file"}:
            status = "missing_files"
        elif states & {"unavailable", "read_error"}:
            status = "access_failed"
        elif states & {"download_budget_exceeded", "size_limit", "size_limit_or_empty", "incomplete_or_budget_limit"}:
            status = "file_limit_exceeded"
        elif "ambiguous_local_file" in states:
            status = "ambiguous_bundle"
    return {"status": status, "stage": "validate", "validation": audit}


async def validate_candidates(report, archive_root, out, *, download_bytes=0):
    out = Path(out).resolve()
    root = Path(archive_root).resolve()
    if not root.is_dir() or out.is_relative_to(root):
        raise ValueError("Archive root must exist and batch output must be outside it")
    out.mkdir(parents=True, exist_ok=False)
    batch = {"status": "running", "samples": [], "status_counts": {}}
    write_json(out / "batch.json", batch)
    seen = set()
    for row in report["samples"]:
        if row.get("status") != "completed" or row.get("include_in_catalog") is not True:
            continue
        sample = row["accession"]
        if sample in seen:
            continue
        seen.add(sample)
        result = {"sample_id": sample, "stage": "prepare"}
        platforms = row.get("rule_prediction", {}).get("platform_candidates")
        requested = report["request"]["platforms"]
        fields = row.get("packet", {}).get("fields", {})
        hd = any(re.search(r"\bvisium\s*hd\b", value, re.I)
                 for values in fields.values() for value in values)
        if platforms == ["Visium"] and "Visium" in requested and not hd:
            result["platform"] = "Visium"
            try:
                if not re.fullmatch(r"GSM[1-9][0-9]*", sample):
                    raise ValueError("Invalid sample accession")
                result.update(await asyncio.to_thread(audit_visium_candidate, row, root, out / sample, download_bytes))
            except (OSError, ValueError, KeyError) as exc:
                result.update(status="processing_failed", error_type=type(exc).__name__)
        elif platforms != ["Xenium"] or "Xenium" not in requested:
            result["status"] = "unsupported_platform"
        else:
            result["platform"] = "Xenium"
            try:
                if not re.fullmatch(r"GSM[1-9][0-9]*", sample):
                    raise ValueError("Invalid sample accession")
                study = row["studies"][0]
                if study not in row["source_series_ids"]:
                    raise ValueError("Sample lacks study association")
                session = PipelineSession(None, study, sample, root, out / sample, report["model"],
                                          download_bytes=download_bytes)
                session.search = {"source": "saved_discovery_selection", "study": study, "sample_id": sample}
                session.screen = {"eligible": True}
                session.fields = {**row["packet"]["fields"],
                                  "Sample_supplementary_file": [asset["url"] for asset in row["assets"]]}
                result["preparation"] = await session.prepare_files()
                result["stage"] = "validate"
                result["validation"] = await session.validate_sample()
                result["status"] = result["validation"]["status"]
            except FileNotFoundError as exc:
                result.update(status="missing_files", error_type=type(exc).__name__)
            except HTTPError as exc:
                result.update(status="access_failed", http_status=exc.code)
            except (URLError, TimeoutError, PermissionError) as exc:
                result.update(status="access_failed", error_type=type(exc).__name__)
            except DownloadBudgetExceeded:
                result["status"] = "download_budget_exceeded"
            except Exception as exc:
                result.update(status="processing_failed", error_type=type(exc).__name__)
        batch["samples"].append(result)
        batch["status_counts"] = dict(Counter(item["status"] for item in batch["samples"]))
        write_json(out / "batch.json", batch)
    batch["status"] = "completed" if all(row["status"] == "correspondence_passed" for row in batch["samples"]) else "completed_with_issues"
    write_json(out / "batch.json", batch)
    return batch
