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
    discovery.add_argument("--organism", default="Homo sapiens")
    discovery.add_argument("--tissue", default="lung")
    discovery.add_argument("--platforms", nargs="+", default=["Visium"])
    discovery.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
    discovery.add_argument("--max-studies", type=bounded_integer(1, 5), default=2)
    discovery.add_argument("--max-samples-per-study", type=bounded_integer(1, 50), default=5)
    discovery.add_argument("--max-total-samples", type=bounded_integer(1, 50), default=10)
    discovery.add_argument("--timeout", type=bounded_integer(30, 1800), default=600)
    discovery.add_argument("--cache", type=Path, default=Path("outputs/discovery_cache"))
    discovery.add_argument("--refresh", action="store_true")
    discovery.add_argument("--resume", type=Path, help="Continue exactly the saved studies and reuse validated completed samples")
    discovery.add_argument("--out", type=Path, required=True)
    discovery.add_argument("--local-manifest", type=Path, help="Optional registered local Xenium inputs for correspondence tools")
    for name in ("check-xenium", "validate-local-agent"):
        local = sub.add_parser(name, help="Registered local Xenium file validation" + (" via original SRAgent supervisor" if name.endswith("agent") else " without a model"))
        local.add_argument("--manifest", type=Path, required=True)
        local.add_argument("--out", type=Path, required=True)
        if name.endswith("agent"):
            local.add_argument("--model", default=os.getenv("SPATIAL_MODEL"))
            local.add_argument("--timeout", type=bounded_integer(30, 1800), default=600)
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
            request = DiscoveryRequest(args.organism, args.tissue, tuple(args.platforms), args.max_studies,
                                       args.max_samples_per_study, args.max_total_samples)
            client = MetadataClient(args.cache, refresh=args.refresh)
            session = (DiscoverySession.resume(client, args.resume, args.out, args.model,
                       max_samples_per_study=args.max_samples_per_study, max_total_samples=args.max_total_samples)
                       if args.resume else DiscoverySession(client, request, args.out, args.model))
            if args.local_manifest:
                from .local_validation import LocalValidationSession
                session.local_validation = LocalValidationSession(args.local_manifest, args.out / "local_validation", args.model)
            report = asyncio.run(discover(session, timeout=args.timeout))
            print(json.dumps({"status": report["status"], "counts": report["counts"], "out": str(args.out)}))
            if report["status"] in {"incomplete", "completed_with_failures"}:
                if report["agent_error"]:
                    print(json.dumps(report["agent_error"]), file=sys.stderr)
                return 2
        elif args.command in {"check-xenium", "validate-local-agent"}:
            from .local_validation import LocalValidationSession, run_validation_agent
            session = LocalValidationSession(args.manifest, args.out, getattr(args, "model", None))
            if args.command == "validate-local-agent":
                from .agents import credentials_present
                if not args.model or not credentials_present():
                    raise RuntimeError("Agent validation requires a model and locally configured provider credential")
                if os.name == "nt":
                    import truststore
                    truststore.inject_into_ssl()
                report = asyncio.run(run_validation_agent(session, timeout=args.timeout))
            else:
                async def validate_registered():
                    session.catalog()
                    for sample_id in session.samples:
                        await session.validate(sample_id)
                    return session.report()
                report = asyncio.run(validate_registered())
            print(json.dumps({key: report[key] for key in ("status", "samples_registered", "samples_validated", "failed_sample_ids")}))
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
            report = validate_visium(args.barcodes, args.positions, args.scalefactors, args.lowres_image, args.matrix)
            write_json(args.out, report)
            print(json.dumps(report))
            if report["issues"]:
                return 2
        else:
            from .upstream import agent_lookup
            write_json(args.out, asyncio.run(agent_lookup(args.accession)))
            print("Saved unreviewed upstream agent output")
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
