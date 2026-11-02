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
            result = asyncio.run(invoke(args.module, args.message, args.model,
                                        timeout=args.timeout, provider=provider()))
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
