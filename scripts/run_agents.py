"""Local hidden-key launcher. Does not store credentials or alter user settings.

The key is read from the interactive terminal into this process only; it is never written
to disk, to a command argument, or to shell history. Works for both providers.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROVIDER_KEY = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
PROVIDER_LABEL = {"openai": "OpenAI", "deepseek": "DeepSeek"}


def resolve_provider(model: str) -> str:
    explicit = (os.getenv("SPATIAL_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if model.strip().lower().startswith("deepseek"):
        return "deepseek"
    if os.getenv("DEEPSEEK_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        return "deepseek"
    return "openai"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", choices=sorted(PROVIDER_KEY))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--module", help="Run one upstream SRAgent search module instead of the benchmark")
    parser.add_argument("--message", help="Instruction for --module")
    parser.add_argument("--prompt-key", action="store_true", help="Prompt for a fresh key even if one exists")
    args = parser.parse_args()
    if not 1 <= args.limit <= 25:
        parser.error("limit must be 1..25")
    if bool(args.module) != bool(args.message):
        parser.error("--module and --message must be supplied together")
    provider = args.provider or resolve_provider(args.model)
    env_name = PROVIDER_KEY[provider]
    previous_key = os.environ.get(env_name)
    prompt_key = args.prompt_key or not previous_key
    try:
        if prompt_key:
            if not sys.stdin.isatty():
                parser.error("Use an interactive local terminal for hidden API-key entry")
            key = getpass.getpass(f"{PROVIDER_LABEL[provider]} API key (hidden; process only): ")
            if not key.strip():
                parser.error("API key is required")
            os.environ[env_name] = key.strip()
            del key
        os.environ.setdefault("SPATIAL_PROVIDER", provider)
        os.chdir(ROOT)
        from sragent_spatial.cli import main as run
        if args.module:
            return run(["search-agent", "--module", args.module, "--message", args.message,
                        "--model", args.model, "--out", f"outputs/v02/modules/{args.module}.json"])
        return run(["benchmark", "--model", args.model, "--limit", str(args.limit),
                    "--out", "outputs/v02/agent_benchmark.json"])
    finally:
        if prompt_key:
            if previous_key is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous_key


if __name__ == "__main__":
    raise SystemExit(main())
