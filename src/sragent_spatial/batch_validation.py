"""Prepare and validate eligible Xenium and Visium samples from a discovery report."""
import asyncio
import re
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError

from .asset_audit import FileFetcher, LocalFileFetcher, audit_sample, file_plan, hd_bin_sizes
from .geo import write_json
from .hd_archive import audit_hd_geo_archive, audit_prepared_archive, spatial_bin_size, validate_hd_bin
from .pipeline import PipelineSession
from .screening import VISIUM_HD
from .xenium_assets import DownloadBudgetExceeded, find_archive


def audit_visium_candidate(row, root, out, download_bytes, *, bin_um=None, remote=None):
    plan = file_plan(row, bin_um=bin_um)
    local = LocalFileFetcher(root, {asset["filename"] for asset in plan["roles"].values()})
    if remote is None:
        remote = FileFetcher(out / "files", download_bytes=download_bytes)

    class Fetcher:
        def fetch(self, accession, asset):
            result = local.fetch(accession, asset)
            if result["status"] == "missing_local_file" and download_bytes:
                return remote.fetch(accession, asset)
            return result

    audit = audit_sample(row, Fetcher(), bin_um=bin_um)
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


def audit_hd_untagged(row, root, out, download_bytes):
    plan = file_plan(row, hd_untagged=True)
    required = {"h5", "positions", "scalefactors", "image"}
    if not plan["metadata_bundle_consistent"]:
        return {"stage": "prepare", "status": "ambiguous_bundle", "bins_um": {}}
    if required - plan["roles"].keys():
        return {"stage": "prepare", "status": "missing_files", "bins_um": {},
                "missing_roles": sorted(required - plan["roles"].keys())}
    local = LocalFileFetcher(root, {plan["roles"][role]["filename"] for role in required})
    remote = FileFetcher(out / "files", download_bytes=download_bytes)
    files, reads = {}, {}
    for role in sorted(required):
        asset = plan["roles"][role]
        info = local.fetch(row["accession"], asset)
        if info["status"] == "missing_local_file" and download_bytes:
            info = remote.fetch(row["accession"], asset)
        reads[role] = info
        if info["status"] == "downloaded":
            files[role] = {"path": Path(info["local_path"]), "member": asset["filename"]}
    if len(files) != len(required):
        states = {info["status"] for info in reads.values()}
        status = "access_failed" if states & {"unavailable", "read_error"} else "missing_files"
        if "ambiguous_local_file" in states:
            status = "ambiguous_bundle"
        elif states & {"size_limit", "download_budget_exceeded", "incomplete_or_budget_limit", "size_limit_or_empty"}:
            status = "file_limit_exceeded"
        return {"stage": "prepare", "status": status, "bins_um": {}, "files": reads}
    size = spatial_bin_size(files["scalefactors"]["path"])
    target = out / f"square_{size:03d}um"
    result = validate_hd_bin(files, target, size)
    write_json(target / "validation.json", result)
    return {"stage": "validate", "status": result["status"], "bins_um": {str(size): result},
            "unlisted_standard_bin_sizes_um": sorted({2, 8, 16} - {size}), "files": reads}


def audit_hd_candidate(row, root, out, download_bytes):
    from urllib.parse import unquote, urlsplit

    if "local_hd_derivative" in row:
        derivative = row["local_hd_derivative"]
        name = derivative["filename"]
        if not re.fullmatch(re.escape(row["accession"]) + r"_derived_016um\.tar\.gz", name):
            raise ValueError("Invalid local HD derivative filename")
        archive = find_archive(root, name)
        if archive is None:
            raise FileNotFoundError("Declared local HD derivative was not found")
        from .xenium_validation import sha256_file
        if sha256_file(archive) != derivative["sha256"]:
            raise ValueError("Local HD derivative checksum mismatch")
        result = audit_prepared_archive(archive, out, {"mode": "local_derivative",
                                                      "filename": name, "sha256": derivative["sha256"]})
        write_json(out / "validation.json", result)
        return result
    sizes = set()
    for asset in row["assets"]:
        name = unquote(urlsplit(asset["url"]).path.rsplit("/", 1)[-1])
        sizes.update(hd_bin_sizes(name))
    if not sizes and any(urlsplit(asset["url"]).path.lower().endswith(".tar.gz") for asset in row["assets"]):
        result = audit_hd_geo_archive(row, root, out, download_bytes)
        write_json(out / "validation.json", result)
        return result
    if not sizes:
        result = audit_hd_untagged(row, root, out, download_bytes)
        write_json(out / "validation.json", result)
        return result
    remote = FileFetcher(out / "files", download_bytes=download_bytes)
    bins = {}
    for size in sorted(sizes):
        bins[str(size)] = audit_visium_candidate(
            row, root, out / f"square_{size:03d}um", download_bytes,
            bin_um=size, remote=remote)
    if not bins:
        status = "missing_files"
    elif all(item["status"] == "correspondence_passed" for item in bins.values()):
        status = "correspondence_passed"
    else:
        status = "completed_with_issues"
    result = {"status": status, "stage": "validate", "bins_um": bins,
              "unlisted_standard_bin_sizes_um": sorted({2, 8, 16} - sizes)}
    write_json(out / "validation.json", result)
    return result


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
        hd = platforms == ["Visium HD"] or any(VISIUM_HD.search(value)
                 for values in fields.values() for value in values)
        if platforms in (["Visium"], ["Visium HD"]) and hd and any(
                platform in requested for platform in ("Visium", "Visium HD")):
            result["platform"] = "Visium HD"
            try:
                if not re.fullmatch(r"GSM[1-9][0-9]*", sample):
                    raise ValueError("Invalid sample accession")
                result.update(await asyncio.to_thread(audit_hd_candidate, row, root, out / sample, download_bytes))
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
        elif platforms == ["Visium"] and "Visium" in requested and not hd:
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
    if not batch["samples"]:
        batch["status"] = "no_candidates"
    else:
        batch["status"] = "completed" if all(
            row["status"] == "correspondence_passed" for row in batch["samples"]
        ) else "completed_with_issues"
    write_json(out / "batch.json", batch)
    return batch
