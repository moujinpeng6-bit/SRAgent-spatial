from __future__ import annotations

import argparse
import asyncio
import json
import sys
import subprocess
import os
from pathlib import Path

from .geo import MetadataClient, export_records, lookup, search, write_json
from .local import compare_sources, inspect_local


def bounded_integer(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"Expected {low}..{high}")
        return number
    return parse


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spatial GEO metadata tools; no sequencing/image downloads")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("lookup", "search"):
        command = sub.add_parser(name)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--cache", type=Path, default=Path("outputs/cache"))
        command.add_argument("--offline", action="store_true")
        command.add_argument("--refresh", action="store_true")
        command.add_argument("--transport", choices=["auto", "urllib", "powershell"], default="auto")
        if name == "lookup":
            command.add_argument("accession")
            command.add_argument("--max-samples", type=bounded_integer(0, 20), default=3)
        else:
            command.add_argument("--organism", default="Homo sapiens")
            command.add_argument("--tissue", default="lung")
            command.add_argument("--platform", default="Visium")
            command.add_argument("--limit", type=bounded_integer(1, 20), default=5)
    inventory = sub.add_parser("inspect-local")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("--max-files", type=bounded_integer(1, 100000), default=5000)
    inventory.add_argument("--out", type=Path, required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--records", type=Path, required=True)
    compare.add_argument("--inventory", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    agent = sub.add_parser("agent-lookup", help="Optional upstream LLM call; may incur provider charges")
    agent.add_argument("accession")
    agent.add_argument("--out", type=Path, required=True)
    spatial_search_cmd = sub.add_parser("spatial-search",
                                        help="Search GEO for spatial series by platform(s); no model, no reads")
    spatial_search_cmd.add_argument("--organism", default="Homo sapiens")
    spatial_search_cmd.add_argument("--tissue", required=True)
    spatial_search_cmd.add_argument("--platforms", nargs="+", required=True)
    spatial_search_cmd.add_argument("--limit", type=bounded_integer(1, 20), default=5)
    spatial_search_cmd.add_argument("--cache", type=Path, default=Path("outputs/cache"))
    spatial_search_cmd.add_argument("--offline", action="store_true")
    spatial_search_cmd.add_argument("--refresh", action="store_true")
    spatial_search_cmd.add_argument("--out", type=Path, required=True)
    discovery = sub.add_parser("discover", help="Original SRAgent supervisor: spatial search -> GSM expansion -> individual screening")
    discovery.add_argument("--organism", default="Homo sapiens", help="Scientific name or * for any species")
    discovery.add_argument("--tissue", default="lung", help="Tissue keyword or * for any tissue")
    discovery.add_argument("--platforms", nargs="+", default=["Visium"], help="Platform names or all for the full supported search catalog")
    discovery.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    discovery.add_argument("--max-studies", type=bounded_integer(1, 5), default=2)
    discovery.add_argument("--max-samples-per-study", type=bounded_integer(1, 50), default=5)
    discovery.add_argument("--max-total-samples", type=bounded_integer(1, 50), default=10)
    discovery.add_argument("--timeout", type=bounded_integer(30, 1800), default=600)
    discovery.add_argument("--cache", type=Path, default=Path("outputs/discovery_cache"))
    discovery.add_argument("--refresh", action="store_true")
    discovery.add_argument("--resume", type=Path, help="Continue exactly the saved studies and reuse validated completed samples")
    discovery.add_argument("--study", help="Restrict the GEO query to one GSE")
    discovery.add_argument("--archive-root", type=Path, help="Prepare and validate screened Xenium, Visium and Visium HD candidates")
    discovery.add_argument("--download-mib", type=bounded_integer(0, 2048), default=0, help="Maximum download per candidate")
    discovery.add_argument("--probe-remote", action="store_true", help="HEAD only for unresolved sample-owned GEO assets")
    discovery.add_argument("--out", type=Path, required=True)
    discovery.add_argument("--local-manifest", type=Path, help="Optional registered local Xenium inputs for correspondence tools")
    batch = sub.add_parser("validate-candidates", help="Validate candidates from an existing discovery run")
    batch.add_argument("--run", type=Path, required=True)
    batch.add_argument("--archive-root", type=Path, required=True)
    batch.add_argument("--probe-remote", action="store_true", help="HEAD only for unresolved sample-owned GEO assets")
    batch.add_argument("--out", type=Path, required=True)
    batch.add_argument("--download-mib", type=bounded_integer(0, 2048), default=0)
    unified = sub.add_parser("validate-spatial-batch", help="Validate GEO discovery results and 10x cache samples together")
    unified.add_argument("--geo-run", type=Path, action="append", default=[])
    unified.add_argument("--archive-root", type=Path)
    unified.add_argument("--10x-cache", dest="tenx_cache", type=Path)
    unified.add_argument("--sample-ids", nargs="+", default=[])
    unified.add_argument("--out", type=Path, required=True)
    unified.add_argument("--download-mib", type=bounded_integer(0, 2048), default=0)
    unified.add_argument("--probe-remote", action="store_true")
    find = sub.add_parser("find-spatial", help="Search GEO and a registered 10x cache, then validate")
    find.add_argument("--organism", required=True)
    find.add_argument("--tissue", required=True)
    find.add_argument("--platforms", nargs="+", choices=["Xenium", "Visium", "Visium HD"], required=True)
    find.add_argument("--source", choices=["geo", "10x", "both"], default="geo")
    find.add_argument("--10x-cache", dest="tenx_cache", type=Path)
    tenx_catalog = find.add_mutually_exclusive_group()
    tenx_catalog.add_argument("--10x-catalog-snapshot", dest="tenx_catalog_snapshot", type=Path,
                              help="Use captured public 10x catalog records")
    tenx_catalog.add_argument("--10x-interactive", dest="tenx_interactive", action="store_true",
                              help="Allow up to five minutes for you to complete 10x verification in Chrome")
    tenx_catalog.add_argument("--10x-fallback-snapshot", dest="tenx_fallback_snapshot", type=Path,
                              help="Use captured catalog records if the live 10x catalog is unavailable")
    find.add_argument("--geo-assets", type=Path, help="Reuse GEO files under this local directory")
    find.add_argument("--probe-remote", action="store_true", help="Check missing GEO links with HEAD")
    find.add_argument("--model", help="Optional model for GEO screening; defaults to rules")
    find.add_argument("--max-studies", type=bounded_integer(1, 5), default=2)
    find.add_argument("--samples-per-study", type=bounded_integer(1, 10), default=2)
    find.add_argument("--download-mib", type=bounded_integer(0, 2048), default=128)
    find.add_argument("--out", type=Path, required=True)
    find.add_argument("--resume", action="store_true")
    pipeline = sub.add_parser("pipeline", help="Targeted GEO search -> same-sample automatic files -> Xenium spatial validation")
    pipeline.add_argument("--study", required=True)
    pipeline.add_argument("--sample", required=True)
    pipeline.add_argument("--archive-root", type=Path, required=True)
    pipeline.add_argument("--out", type=Path, required=True)
    pipeline.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    pipeline.add_argument("--timeout", type=bounded_integer(30, 1800), default=900)
    pipeline.add_argument("--download-mib", type=bounded_integer(0, 2048), default=0)
    for name in ("check-xenium", "validate-local-agent"):
        local = sub.add_parser(name, help="Registered local Xenium file validation" + (" via original SRAgent supervisor" if name.endswith("agent") else " without a model"))
        if name.endswith("agent"):
            source = local.add_mutually_exclusive_group(required=True)
            source.add_argument("--manifest", type=Path)
            source.add_argument("--10x-cache", dest="tenx_cache", type=Path)
            local.add_argument("--sample-id", help="Exact sample directory ID in the 10x cache")
        else:
            local.add_argument("--manifest", type=Path, required=True)
        local.add_argument("--out", type=Path, required=True)
        if name.endswith("agent"):
            local.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
            local.add_argument("--timeout", type=bounded_integer(30, 1800), default=600)
    tenx_batch = sub.add_parser("validate-10x-batch", help="Validate and resume a batch of cached 10x images")
    tenx_batch.add_argument("--10x-cache", dest="tenx_cache", type=Path, required=True)
    tenx_batch.add_argument("--sample-ids", nargs="+", required=True)
    tenx_batch.add_argument("--out", type=Path, required=True)
    tenx_batch.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    tenx_batch.add_argument("--timeout", type=bounded_integer(30, 1800), default=600)
    tenx_batch.add_argument("--resume", action="store_true")
    tenx_batch.add_argument("--local-tools", action="store_true", help="Run the same preparation and validation tools directly")
    audit = sub.add_parser("audit-assets", help="Check each selected GSM's Visium expression/position/image bundle")
    audit.add_argument("--run", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    audit.add_argument("--workers", type=bounded_integer(1, 3), default=3)
    audit.add_argument("--local-root", type=Path, help="Read matching original GEO filenames under this local directory")
    screening = sub.add_parser("screen-samples", help="Classify frozen GSM sample packets with evidence")
    screening.add_argument("--materials", type=Path, required=True)
    screening.add_argument("--out", type=Path, required=True)
    evaluation = sub.add_parser("benchmark", help="Rules/original/spatial/sragent comparison on the same development cases")
    evaluation.add_argument("--cases", type=Path, default=Path("evaluation/lung_spatial_development.json"))
    evaluation.add_argument("--methods", nargs="+", choices=["rules", "original_agent", "spatial_agent", "sragent"],
                            default=["rules", "original_agent", "spatial_agent"])
    evaluation.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    evaluation.add_argument("--limit", type=bounded_integer(1, 100))
    evaluation.add_argument("--timeout", type=bounded_integer(10, 600), default=120)
    evaluation.add_argument("--out", type=Path, required=True)
    sub.add_parser("doctor", help="Report credential/dependency presence without printing secrets or calling APIs")
    registry = sub.add_parser("modules", help="List every upstream SRAgent search/fetch module and its readiness")
    registry.add_argument("--out", type=Path)
    search_agent = sub.add_parser("search-agent",
                                  help="Run one upstream SRAgent module as a tool; may incur provider charges")
    search_agent.add_argument("--module", required=True)
    search_agent.add_argument("--message", required=True)
    search_agent.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    search_agent.add_argument("--timeout", type=bounded_integer(10, 600), default=120)
    search_agent.add_argument("--out", type=Path, required=True)
    spatial = sub.add_parser("check-visium", help="Basic barcode/coordinate and optional low-resolution bounds checks")
    spatial.add_argument("--barcodes", type=Path, required=True)
    spatial.add_argument("--positions", type=Path, required=True)
    spatial.add_argument("--scalefactors", type=Path)
    spatial.add_argument("--lowres-image", type=Path)
    spatial.add_argument("--matrix", type=Path)
    spatial.add_argument("--bin-um", type=int, choices=(2, 8, 16, 24))
    spatial.add_argument("--full-matrix", action="store_true", help="Check every integer Matrix Market entry")
    spatial.add_argument("--overlay", type=Path, help="Write a new PNG with sampled in-tissue positions")
    spatial.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"lookup", "search"}:
            client = MetadataClient(args.cache, offline=args.offline, transport=args.transport, refresh=args.refresh)
            if args.command == "lookup":
                records, report = lookup(client, args.accession, args.max_samples)
            else:
                records, report = search(client, args.organism, args.tissue, args.platform, args.limit)
            export_records(args.out, records, report, client)
            print(json.dumps({"records": len(records), "out": str(args.out)}, ensure_ascii=True))
        elif args.command == "spatial-search":
            from .spatial_search import spatial_search
            client = MetadataClient(args.cache, offline=args.offline, transport="auto", refresh=args.refresh)
            records, report = spatial_search(client, args.organism, args.tissue, args.platforms, args.limit)
            export_records(args.out, records, report, client)
            print(json.dumps({"records": len(records), "total_hits": report["total_hits"],
                              "platforms": report["platforms"], "out": str(args.out)}, ensure_ascii=True))
        elif args.command == "discover":
            from .agents import credentials_present
            from .discovery import DiscoveryRequest, DiscoverySession, discover
            if not args.model or not credentials_present():
                raise RuntimeError("Discovery requires an explicit model and a locally configured provider API key")
            if os.name == "nt":
                import truststore
                truststore.inject_into_ssl()
            from .spatial_search import PLATFORM_KEYS
            platforms = PLATFORM_KEYS if args.platforms == ["all"] else tuple(args.platforms)
            request = DiscoveryRequest(args.organism, args.tissue, platforms, args.max_studies,
                                       args.max_samples_per_study, args.max_total_samples, args.study)
            client = MetadataClient(args.cache, refresh=args.refresh)
            session = (DiscoverySession.resume(client, args.resume, args.out, args.model,
                       max_samples_per_study=args.max_samples_per_study, max_total_samples=args.max_total_samples)
                       if args.resume else DiscoverySession(client, request, args.out, args.model))
            if args.local_manifest:
                from .local_validation import LocalValidationSession
                session.local_validation = LocalValidationSession(args.local_manifest, args.out / "local_validation", args.model)
            async def discover_and_validate():
                report = await discover(session, timeout=args.timeout)
                if args.archive_root:
                    from .batch_validation import validate_candidates
                    report["asset_batch"] = await validate_candidates(
                        report, args.archive_root, args.out / "asset_batch",
                        download_bytes=args.download_mib * 1024**2, probe_remote=args.probe_remote)
                    write_json(args.out / "run.json", report)
                return report
            report = asyncio.run(discover_and_validate())
            if "asset_batch" in report:
                print(json.dumps({"asset_status_counts": report["asset_batch"]["status_counts"]}))
            print(json.dumps({"status": report["status"], "counts": report["counts"], "out": str(args.out)}))
            if report["status"] in {"incomplete", "completed_with_failures"}:
                if report["agent_error"]:
                    print(json.dumps(report["agent_error"]), file=sys.stderr)
                return 2
        elif args.command == "find-spatial":
            from .agents import credentials_present
            from .find_spatial import find_spatial
            if args.model and not credentials_present():
                raise RuntimeError("Model screening requires a locally configured provider API key")
            if os.name == "nt":
                import truststore
                truststore.inject_into_ssl()
            report = asyncio.run(find_spatial(args.organism, args.tissue, args.platforms, args.tenx_cache,
                                              args.out, model=args.model, download_bytes=args.download_mib * 1024**2,
                                              max_studies=args.max_studies, samples_per_study=args.samples_per_study,
                                              resume=args.resume, source=args.source,
                                              catalog_snapshot=args.tenx_catalog_snapshot,
                                              tenx_interactive=args.tenx_interactive,
                                              catalog_fallback=args.tenx_fallback_snapshot,
                                              geo_assets=args.geo_assets, probe_remote=args.probe_remote))
            print(json.dumps({"status": report["status"], **report["totals"]}))
            if report["status"] != "completed":
                return 2
        elif args.command == "validate-spatial-batch":
            from .unified_batch import validate_sources
            if (not (args.geo_run or args.sample_ids) or bool(args.geo_run) != bool(args.archive_root)
                    or bool(args.sample_ids) != bool(args.tenx_cache)):
                raise ValueError("Supply --geo-run with --archive-root and/or --10x-cache with --sample-ids")
            if os.name == "nt" and (args.sample_ids or args.download_mib or args.probe_remote):
                import truststore
                truststore.inject_into_ssl()
            reports = [json.loads(path.read_text(encoding="utf-8-sig")) for path in args.geo_run]
            report = asyncio.run(validate_sources(reports, args.archive_root, args.tenx_cache, args.sample_ids,
                                                  args.out, download_bytes=args.download_mib * 1024**2,
                                                  probe_remote=args.probe_remote))
            print(json.dumps({"status": report["status"], **report["totals"]}))
            if report["status"] != "completed":
                return 2
        elif args.command == "validate-candidates":
            from .batch_validation import validate_candidates
            if os.name == "nt" and (args.download_mib or args.probe_remote):
                import truststore
                truststore.inject_into_ssl()
            report = asyncio.run(validate_candidates(
                json.loads(args.run.read_text(encoding="utf-8")), args.archive_root, args.out,
                download_bytes=args.download_mib * 1024**2, probe_remote=args.probe_remote))
            print(json.dumps({"status": report["status"], "status_counts": report["status_counts"]}))
        elif args.command == "pipeline":
            from .agents import credentials_present
            from .pipeline import PipelineSession, run_pipeline
            if not args.model or not credentials_present():
                raise RuntimeError("Pipeline requires a model and local provider credential")
            if os.name == "nt":
                import truststore
                truststore.inject_into_ssl()
            client = MetadataClient(args.out / "metadata_cache", refresh=True)
            session = PipelineSession(client, args.study, args.sample, args.archive_root, args.out,
                                      args.model, download_bytes=args.download_mib*1024**2)
            report = asyncio.run(run_pipeline(session, timeout=args.timeout))
            print(json.dumps({key: report[key] for key in ("status", "study", "sample_id", "agent_pipeline_verified", "elapsed_seconds")}))
            if report["status"] != "completed":
                return 2
        elif args.command in {"check-xenium", "validate-local-agent"}:
            from .local_validation import LocalValidationSession, TenxCacheSession, run_validation_agent
            if args.command == "validate-local-agent":
                from .agents import credentials_present
                if not args.model or not credentials_present():
                    raise RuntimeError("Agent validation requires a model and locally configured provider credential")
                if os.name == "nt":
                    import truststore
                    truststore.inject_into_ssl()
                if bool(args.tenx_cache) != bool(args.sample_id):
                    raise ValueError("--10x-cache and --sample-id must be supplied together")
                session = (TenxCacheSession(args.tenx_cache, args.sample_id, args.out, args.model)
                           if args.tenx_cache else LocalValidationSession(args.manifest, args.out, args.model))
                report = asyncio.run(run_validation_agent(session, timeout=args.timeout))
            else:
                session = LocalValidationSession(args.manifest, args.out)
                async def validate_registered():
                    session.catalog()
                    for sample_id in session.samples:
                        await session.validate(sample_id)
                    return session.report()
                report = asyncio.run(validate_registered())
            print(json.dumps({key: report[key] for key in ("status", "samples_registered", "samples_validated", "failed_sample_ids")}))
            if report["status"] != "completed":
                return 2
        elif args.command == "validate-10x-batch":
            from .agents import credentials_present
            from .tenx_batch import run_batch
            if not args.local_tools and (not args.model or not credentials_present()):
                raise RuntimeError("Batch validation requires a model and locally configured provider credential")
            if os.name == "nt":
                import truststore
                truststore.inject_into_ssl()
            report = asyncio.run(run_batch(args.tenx_cache, args.sample_ids, args.out,
                                           None if args.local_tools else args.model,
                                           timeout=args.timeout, resume=args.resume, local_tools=args.local_tools))
            print(json.dumps({"status": report["status"],
                              "samples": {key: row["status"] for key, row in report["samples"].items()}}))
            if report["status"] != "completed":
                return 2
        elif args.command == "audit-assets":
            from .asset_audit import audit_run
            report = audit_run(args.run, args.out, workers=args.workers, local_root=args.local_root)
            print(json.dumps({key: report[key] for key in ("expected_samples", "audited_samples", "status_counts")}))
            if report["status_counts"].get("basic_correspondence_passed", 0) != report["expected_samples"]:
                return 2
        elif args.command == "inspect-local":
            report = inspect_local(args.root, args.max_files)
            write_json(args.out, report)
            print(json.dumps({key: report[key] for key in ("file_count", "truncated", "kind_counts", "incomplete_count")}))
        elif args.command == "compare":
            report = compare_sources(json.loads(args.records.read_text(encoding="utf-8")),
                                     json.loads(args.inventory.read_text(encoding="utf-8")))
            write_json(args.out, report)
            print(json.dumps({"assets_with_local_match": report["assets_with_local_match"]}))
        elif args.command == "screen-samples":
            from .screening import screen_sample
            packets = json.loads(args.materials.read_text(encoding="utf-8"))
            write_json(args.out, [screen_sample(packet) for packet in packets])
            print(json.dumps({"samples": len(packets), "out": str(args.out)}))
        elif args.command == "benchmark":
            from .agents import credentials_present, provider
            from .evaluation import benchmark, load_cases
            paid = any(m != "rules" for m in args.methods)
            if credentials_present() and args.model and args.limit is None and paid:
                raise ValueError("For paid Agent runs, specify an explicit --limit (start with 1)")
            if os.name == "nt" and credentials_present() and paid:
                try:
                    import truststore
                except ImportError as exc:
                    raise RuntimeError("Install the optional agent dependencies for Windows system certificate validation") from exc
                # Application entry point only; keep TLS verification using native trusted roots.
                truststore.inject_into_ssl()
            report = asyncio.run(benchmark(load_cases(args.cases), args.methods, args.model,
                                          args.limit, args.timeout))
            write_json(args.out, report)
            print(json.dumps(report["summary"]))
            for row in report["results"]:
                if row["status"] == "failed":
                    print(json.dumps({key: row[key] for key in (
                        "accession", "method", "failure_type", "http_status", "provider_error_code", "failure_hint"
                    ) if key in row}), file=sys.stderr)
            if any(value["skipped"] or value["failed"] for value in report["summary"].values()):
                return 2
        elif args.command == "doctor":
            from .agents import doctor
            print(json.dumps(doctor()))
        elif args.command == "modules":
            from .upstream_registry import describe
            rows = describe()
            if args.out:
                write_json(args.out, rows)
            for row in rows:
                ready = {True: "yes", False: "no", None: "unknown"}[row["setup_ready"]]
                print(f"{row['key']:<15} {row['spatial_status']:<8} setup_ready={ready:<8} {row['role']}")
        elif args.command == "search-agent":
            from .agents import credentials_present, provider
            from .upstream_registry import invoke
            if not credentials_present():
                raise RuntimeError(f"No credential configured for provider {provider()!r}; see docs/deepseek.md")
            if os.name == "nt":
                import truststore
                truststore.inject_into_ssl()
            try:
                result = asyncio.run(invoke(args.module, args.message, args.model,
                                            timeout=args.timeout, provider=provider()))
            except Exception as exc:
                from .evaluation import failure_details
                failure = {"module": args.module, "status": "failed", **failure_details(exc)}
                write_json(args.out, failure)
                print(json.dumps(failure), file=sys.stderr)
                return 2
            write_json(args.out, result)
            print(json.dumps({"module": result["module"], "elapsed_seconds": result["elapsed_seconds"],
                              "output_chars": len(result["output"])}))
        elif args.command == "check-visium":
            from .spatial import validate_visium
            if bool(args.scalefactors) != bool(args.lowres_image):
                raise ValueError("Supply scalefactors and lowres-image together")
            if args.overlay and args.overlay.resolve() == args.out.resolve():
                raise ValueError("Overlay and JSON report must have different paths")
            report = validate_visium(args.barcodes, args.positions, args.scalefactors, args.lowres_image,
                                     args.matrix, bin_um=args.bin_um, full_matrix=args.full_matrix,
                                     overlay=args.overlay)
            write_json(args.out, report)
            print(json.dumps(report))
            if report["issues"]:
                return 2
        else:
            from .upstream import agent_lookup
            write_json(args.out, asyncio.run(agent_lookup(args.accession)))
            print("Saved unreviewed upstream agent output")
        return 0
    except (ValueError, OSError, EOFError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
