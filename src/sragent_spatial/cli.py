from __future__ import annotations

import argparse
import asyncio
import json
import sys
import subprocess
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
        else:
            from .upstream import agent_lookup
            write_json(args.out, asyncio.run(agent_lookup(args.accession)))
            print("Saved unreviewed upstream agent output")
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
