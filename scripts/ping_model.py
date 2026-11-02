"""Minimal provider ping: proves key + endpoint + network before SRAgent is involved.

Usage:
    python scripts/ping_model.py                      # uses SPATIAL_PROVIDER/SPATIAL_MODEL
    python scripts/ping_model.py --list               # ask the endpoint which model ids exist
    python scripts/ping_model.py --model deepseek-flash
    python scripts/ping_model.py --provider openai --model gpt-4o-mini

Prints only status, model id, latency and a short reply. Never prints the key.
"""

import argparse
import asyncio
import os
import sys
import time

PROVIDER_KEY = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
DEFAULT_MODEL = {"openai": "gpt-4o-mini", "deepseek": "deepseek-flash"}


def list_models() -> int:
    """Ask the OpenAI-compatible endpoint for its model ids."""
    import httpx

    base = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    key = os.getenv("DEEPSEEK_API_KEY") or ""
    if not key:
        print("[fail] DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        response = httpx.get(root + "/models", headers={"Authorization": "Bearer " + key.strip()}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if response.status_code != 200:
        print(f"[fail] HTTP {response.status_code}", file=sys.stderr)
        return 1
    for item in response.json().get("data", []):
        print(f"{item.get('id'):<24} {item.get('name', '')} context={item.get('context_window')} "
              f"max_output={item.get('max_output_tokens')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=sorted(PROVIDER_KEY))
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--list", action="store_true", help="List model ids exposed by the endpoint")
    args = parser.parse_args()
    if args.list:
        return list_models()

    provider = args.provider or (os.getenv("SPATIAL_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai"
    key_name = PROVIDER_KEY[provider]
    if not os.getenv(key_name):
        print(f"[fail] {key_name} is not set for provider {provider!r}", file=sys.stderr)
        return 2

    model_id = args.model or os.getenv("SPATIAL_MODEL") or DEFAULT_MODEL[provider]
    started = time.monotonic()
    try:
        if provider == "deepseek":
            from sragent_spatial.deepseek_model import build
            client = build(model_id, temperature=0, max_tokens=16, timeout=args.timeout)
        else:
            from langchain_openai import ChatOpenAI
            client = ChatOpenAI(model=model_id, temperature=0, max_tokens=16, timeout=args.timeout)
        reply = asyncio.run(client.ainvoke("Reply with the single word: pong"))
    except Exception as exc:  # noqa: BLE001 - isolate provider/network problems
        status = getattr(exc, "status_code", None)
        print(f"[fail] provider={provider} model={model_id} "
              f"error={type(exc).__name__} status={status}", file=sys.stderr)
        print("       check: key valid, account balance, model id, network/proxy", file=sys.stderr)
        return 1

    text = getattr(reply, "content", str(reply)).strip()
    print(f"[ok] provider={provider} model={model_id} "
          f"latency={time.monotonic() - started:.2f}s reply={text[:60]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
