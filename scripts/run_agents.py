"""Local hidden-key launcher. Does not store credentials or alter user settings."""

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.limit <= 25:
        parser.error("limit must be 1..25")
    existed = bool(os.getenv("OPENAI_API_KEY"))
    try:
        if not existed:
            if not sys.stdin.isatty():
                parser.error("Use an interactive local terminal for hidden API-key entry")
            key = getpass.getpass("OpenAI API key (hidden; process only): ")
            if not key.strip():
                parser.error("API key is required")
            os.environ["OPENAI_API_KEY"] = key.strip()
            del key
        os.chdir(ROOT)
        from sragent_spatial.cli import main as run
        return run(["benchmark", "--model", args.model, "--limit", str(args.limit),
                    "--out", "outputs/v02/agent_benchmark.json"])
    finally:
        if not existed:
            os.environ.pop("OPENAI_API_KEY", None)


if __name__ == "__main__":
    raise SystemExit(main())
