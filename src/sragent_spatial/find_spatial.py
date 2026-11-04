"""Search by tissue and platform, then validate the frozen candidates."""
import asyncio
import json
import re
from pathlib import Path

from .discovery import DiscoveryRequest, DiscoverySession
from .geo import MetadataClient
from .screening import screen_sample
from .tenx_batch import batch_lock, checkpoint
from .unified_batch import summarize, validate_sources


def cache_samples(cache, organism, tissue, platforms):
    if cache is None or organism.casefold() != "homo sapiens" or "Xenium" not in platforms:
        return []
    catalog = json.loads((cache / "sample_metadata.json").read_text(encoding="utf-8-sig"))
    rows = {sample["row"] for sample in catalog["samples"]
            if sample.get("source") == "10X" and sample.get("organ", "").casefold() == tissue.casefold()
            and re.search(r"\bhuman\b", sample.get("dataset", ""), re.I)}
    manifest = json.loads((cache / "download_manifest.json").read_text(encoding="utf-8-sig"))
    ids = []
    for asset in manifest:
        match = re.fullmatch(r"samples/([A-Za-z0-9_-]+)/morphology_focus\.ome\.tif", asset["relative"])
        if asset.get("row") in rows and asset.get("type") == "zip" and match and match[1] not in ids:
            ids.append(match[1])
    return sorted(ids)


async def rule_predictor(packet, _method, _model):
    return screen_sample(packet)


async def find_spatial(organism, tissue, platforms, cache, out, *, model=None, download_bytes=0,
                       max_studies=2, samples_per_study=2, resume=False, source="geo", catalog_snapshot=None):
    out = Path(out).resolve()
    cache = Path(cache).resolve() if cache else None
    if cache and (not cache.is_dir() or out.is_relative_to(cache)):
        raise ValueError("10x cache must exist and output must be outside it")
    platforms = list(dict.fromkeys(platforms))
    requests = [DiscoveryRequest(organism, tissue, (platform,), max_studies, samples_per_study,
                                 max_studies * samples_per_study) for platform in platforms] if source != "10x" else []
    request = {"organism": organism, "tissue": tissue, "platforms": platforms, "model": model,
               "cache": str(cache) if cache else None, "download_bytes": download_bytes,
               "max_studies": max_studies, "samples_per_study": samples_per_study}
    if source != "geo":
        request["source"] = source
    if catalog_snapshot is not None:
        if source == "geo":
            raise ValueError("A 10x catalog snapshot requires --source 10x or both")
        request["tenx_catalog_mode"] = "snapshot"
    out.mkdir(parents=True, exist_ok=resume)
    with batch_lock(out):
        request_path = out / "request.json"
        if resume:
            if json.loads(request_path.read_text(encoding="utf8")) != request:
                raise ValueError("Resume requires the original search and acquisition settings")
        else:
            checkpoint(request_path, request)
        ids_path = out / "10x_selection.json"
        if not ids_path.exists():
            checkpoint(ids_path, {"sample_ids": cache_samples(cache, organism, tissue, platforms)})
        ids = json.loads(ids_path.read_text(encoding="utf8"))["sample_ids"]
        client = MetadataClient(out / "geo_cache")
        reports = []
        for index, query in enumerate(requests, 1):
            frozen = out / f"discovery_{index}.json"
            if not frozen.exists():
                session = DiscoverySession(client, query, out / f"search_{index}", model or "rules",
                                           predictor=None if model else rule_predictor)
                await session.search()
                for study in session.studies:
                    await session.expand(study)
                    await session.screen(study)
                report = session.report()
                report["screening_mode"] = "rules_and_model" if model else "rules_only"
                if not model:
                    for row in report["samples"]:
                        if row.get("status") == "completed":
                            row["selection_scope"] = "rules_plus_requested_platform; metadata_only"
                checkpoint(frozen, report)
            reports.append(json.loads(frozen.read_text(encoding="utf8")))
        assets = out / "local_geo_assets"
        assets.mkdir(exist_ok=True)
        result = await validate_sources(reports, assets, cache, ids, out / "validation",
                                        download_bytes=download_bytes, resume=resume)
        if source != "geo":
            from .tenx_online import discover_10x, read_catalog_snapshot, validate_online

            online = out / "10x_online"
            online.mkdir(exist_ok=True)
            catalog = online / "catalog.json"
            mode = "snapshot" if catalog_snapshot is not None else "live"
            try:
                if not catalog.exists():
                    filters = (organism, tissue, platforms, max_studies)
                    if catalog_snapshot is None:
                        matches = await asyncio.to_thread(discover_10x, *filters)
                    else:
                        matches = read_catalog_snapshot(catalog_snapshot, *filters)
                    checkpoint(catalog, {"samples": matches})
                matches = json.loads(catalog.read_text(encoding="utf8"))["samples"]
                for row in matches:
                    sample_out = online / row["sample_id"]
                    sample_result = sample_out / "result.json"
                    measured = (json.loads(sample_result.read_text(encoding="utf8"))
                                if sample_result.exists() else None)
                    if measured is None or (resume and measured["status"] in {
                        "processing_failed", "download_budget_exceeded",
                    }):
                        sample_out.mkdir(exist_ok=True)
                        measured = await asyncio.to_thread(validate_online, row, sample_out, download_bytes)
                        checkpoint(sample_result, measured)
                    result["samples"].append(measured)
                result["10x_catalog"] = {"status": "completed", "found": len(matches), "mode": mode}
            except (ImportError, OSError, RuntimeError) as exc:
                result["10x_catalog"] = {"status": "access_failed", "error_type": type(exc).__name__,
                                         "reason": str(exc), "mode": mode}
            summarize(result)
            if result["10x_catalog"]["status"] != "completed":
                result["status"] = "completed_with_issues"
        result["discovery"] = [{"request": item["request"], "status": item["status"],
                                "counts": item["counts"], "search": item["search"],
                                "screening_mode": item.get("screening_mode", "rules_and_model" if model else "rules_only")} for item in reports]
        result["resumed"] = resume
        checkpoint(out / "batch.json", result)
        return result
