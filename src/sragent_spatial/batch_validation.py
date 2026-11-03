"""Prepare and validate eligible Xenium samples from a discovery report."""
import re
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError

from .geo import write_json
from .pipeline import PipelineSession
from .xenium_assets import DownloadBudgetExceeded


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
        if platforms != ["Xenium"] or "Xenium" not in report["request"]["platforms"]:
            result["status"] = "unsupported_platform"
        else:
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
