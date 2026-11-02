"""Lazy access to every upstream SRAgent agent module (search, fetch, ontology).

The registry is metadata-only: importing this module must not import SRAgent, so
``run.py doctor`` and ``run.py modules`` stay usable without the optional dependency.
Each module is imported on first use. Wrappers have different input schemas:
message, messages, tissue_description, disease_description and accession.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Module:
    key: str
    module: str
    factory: str
    tool: str
    role: str
    # direct = usable for spatial work as-is, rewrite = upstream logic is single-cell specific,
    # blocked = external credential/tooling required before it can run
    spatial_status: str
    setup: str = ""
    note: str = ""


MODULES: tuple[Module, ...] = (
    Module("esearch", "SRAgent.agents.esearch", "create_esearch_agent", "invoke_esearch_agent",
           "Entrez esearch over SRA/GEO", "rewrite",
           note="esearch_scrna hard-codes 10x Genomics, paired layout, Illumina, public access "
                "and excludes Smart-seq/MARS-seq; imaging-based spatial assays need a new query"),
    Module("esummary", "SRAgent.agents.esummary", "create_esummary_agent", "invoke_esummary_agent",
           "Entrez esummary document summaries", "direct"),
    Module("efetch", "SRAgent.agents.efetch", "create_efetch_agent", "invoke_efetch_agent",
           "Entrez efetch full records", "direct"),
    Module("elink", "SRAgent.agents.elink", "create_elink_agent", "invoke_elink_agent",
           "Entrez elink cross-database links", "direct",
           note="the main route from an SRX to its GEO series and publications"),
    Module("entrez", "SRAgent.agents.entrez", "create_entrez_agent", "invoke_entrez_agent",
           "Supervisor over the four Entrez sub-agents", "direct"),
    Module("entrez_convert", "SRAgent.agents.entrez_convert", "create_entrez_convert_agent",
           "invoke_entrez_convert_agent", "Accession conversion (GSE/GSM/SRX/SRR)", "direct"),
    Module("ncbi_fetch", "SRAgent.agents.ncbi_fetch", "create_ncbi_fetch_agent",
           "invoke_ncbi_fetch_agent", "GEO/BioSample/BioProject/PubMed page fetch", "direct",
           note="already used by the v0.2 spatial_agent path"),
    Module("tissue_ontology", "SRAgent.agents.tissue_ontology", "create_tissue_ontology_agent",
           "invoke_tissue_ontology_agent", "Uberon term selection and neighbours", "direct",
           setup="ontology-store",
           note="vector search needs cached Uberon data and OpenAI text-embedding-3-small; resources load on tool use"),
    Module("disease_ontology", "SRAgent.agents.disease_ontology", "create_disease_ontology_agent",
           "invoke_disease_ontology_agent", "MONDO term selection and neighbours", "direct",
           setup="ontology-store",
           note="vector search needs cached MONDO data and OpenAI text-embedding-3-small; resources load on tool use"),
    Module("papers", "SRAgent.agents.papers", "create_papers_agent", "invoke_papers_agent",
           "PubMed lookup returning DOIs", "direct",
           note="the Agent returns DOI strings; downloading PDFs is a separate upstream helper"),
    Module("bigquery", "SRAgent.agents.bigquery", "create_bigquery_agent", "invoke_bigquery_agent",
           "SRA metadata via BigQuery", "blocked", setup="gcp",
           note="needs a Google Cloud project and credentials; omitted from the first milestone"),
    Module("sequences", "SRAgent.agents.sequences", "create_sequences_agent", "invoke_sequences_agent",
           "sra-stat / fastq-dump on run data", "blocked", setup="sra-tools",
           note="needs SRA Toolkit and disk; raw download is out of scope for the catalogue stage"),
    Module("find_datasets", "SRAgent.agents.find_datasets", "create_find_datasets_agent",
           "invoke_find_datasets_agent", "Discovery workflow entry point", "rewrite",
           note="inherits the single-cell esearch query; also uses structured output in its workflow"),
    Module("sragent", "SRAgent.agents.sragent", "create_sragent_agent", "invoke_sragent_agent",
           "Full supervisor: Entrez + ncbi_fetch + BigQuery + sequences", "direct",
           setup="supervisor",
           note="the complete hierarchy the v0.2 baseline did not use"),
)

BY_KEY = {module.key: module for module in MODULES}

#: Modules that answer questions about public metadata without credentials or new tooling.
READ_ONLY = ("esearch", "esummary", "efetch", "elink", "entrez", "entrez_convert",
             "ncbi_fetch", "tissue_ontology", "disease_ontology")


def _probe(setup: str) -> bool | None:
    """Best-effort precondition check. ``None`` means it cannot be verified without importing."""
    if not setup:
        return True
    if setup == "gcp":
        from .dependencies import dependency_report
        return dependency_report()["bigquery"]["requirements_detected"]
    if setup == "sra-tools":
        from .dependencies import dependency_report
        return dependency_report()["sequences"]["requirements_detected"]
    if setup == "supervisor":
        from .dependencies import dependency_report
        deps = dependency_report()
        return deps["bigquery"]["requirements_detected"] and deps["sequences"]["requirements_detected"]
    if setup in {"ontology-store", "pdf-tools"}:
        return None
    return None


def describe() -> list[dict]:
    rows = []
    for module in MODULES:
        row = asdict(module)
        row["setup_ready"] = _probe(module.setup)
        row["read_only"] = module.key in READ_ONLY
        row["setup_scope"] = "local_requirements_only_not_live_validation"
        if module.key in {"tissue_ontology", "disease_ontology"}:
            from .dependencies import dependency_report
            row["setup_ready"] = dependency_report()[module.key]["requirements_detected"]
        if row["spatial_status"] == "blocked" and row["setup_ready"]:
            row["spatial_status"] = "direct"
        rows.append(row)
    return rows


def summary() -> dict:
    rows = describe()
    return {"total": len(rows),
            "direct": sum(r["spatial_status"] == "direct" for r in rows),
            "rewrite": sum(r["spatial_status"] == "rewrite" for r in rows),
            "blocked": sum(r["spatial_status"] == "blocked" for r in rows),
            "not_ready": [r["key"] for r in rows if r["setup_ready"] is False]}


def load(key: str, model: str | None = None, *, provider: str | None = None):
    """Import and build one upstream agent tool, installing DeepSeek routing first."""
    if key not in BY_KEY:
        raise ValueError(f"Unknown SRAgent module {key!r}; expected one of {', '.join(BY_KEY)}")
    from .dependencies import activate_toolkit
    activate_toolkit()
    provider = (provider or os.getenv("SPATIAL_PROVIDER")
                or ("deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai")).strip().lower()
    if provider not in {"openai", "deepseek"}:
        raise ValueError("Provider must be openai or deepseek")
    os.environ.setdefault("DYNACONF_SETTINGS_PATH", str(Path(__file__).with_name("agent_settings.yml")))
    if provider == "deepseek":
        from .deepseek_model import install, is_installed
        if not is_installed():
            install()
    module = BY_KEY[key]
    try:
        imported = importlib.import_module(module.module)
    except Exception as exc:  # import-time side effects vary per module
        raise RuntimeError(
            f"{key}: importing {module.module} failed ({type(exc).__name__}). "
            f"{('External setup: ' + module.setup) if module.setup else ''}"
        ) from exc
    factory = getattr(imported, module.factory, None)
    if factory is None:
        raise RuntimeError(f"{key}: {module.module} has no {module.factory}")
    from .deepseek_model import model_context
    with model_context(model) if provider == "deepseek" and model else nullcontext():
        return factory(model_name=model) if model else factory()


def payload_for(tool, message: str) -> dict:
    """Build the argument dict from the tool's own schema instead of guessing."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Module input must be a nonempty string")
    args = getattr(tool, "args", None) or {}
    if not isinstance(args, dict):
        args = {}
    if "message" in args or not args:
        return {"message": message}
    if "task" in args:
        return {"task": message}
    if "messages" in args:
        from langchain_core.messages import HumanMessage
        return {"messages": [HumanMessage(content=message)]}
    for name in ("tissue_description", "disease_description", "accession"):
        if name in args:
            return {name: message}
    raise RuntimeError(f"Unsupported tool schema for {getattr(tool, 'name', tool)!r}: {sorted(args)}")


def as_text(result) -> str:
    """Normalise whatever an upstream agent returns into plain text."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(str(item.get("text", "")) if isinstance(item, dict)
                         else str(getattr(item, "content", item)) for item in result)
    if isinstance(result, dict):
        content = result.get("messages") or result.get("content") or result
    else:
        content = getattr(result, "content", result)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(getattr(item, "content", item)))
        return "\n".join(part for part in parts if part)
    return str(content)


async def invoke(key: str, message: str, model: str | None = None, *, timeout: float = 120.0,
                 provider: str | None = None) -> dict:
    """Run one upstream module as a tool and return text plus provenance."""
    started = time.monotonic()
    from langchain_core.callbacks import BaseCallbackHandler

    events = []

    class Trace(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            events.append({"event": "start", "name": (serialized or {}).get("name", "unknown"), "run_id": str(run_id)})

        def on_tool_end(self, output, *, run_id, **kwargs):
            events.append({"event": "returned", "run_id": str(run_id)})

        def on_tool_error(self, error, *, run_id, **kwargs):
            events.append({"event": "error", "error_type": type(error).__name__, "run_id": str(run_id)})

    if os.getenv("GCP_PROJECT_ID") and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ["GCP_PROJECT_ID"]
    tool = load(key, model, provider=provider)
    config = {"callbacks": [Trace()], "recursion_limit": 16, "max_concurrency": 1}
    chosen_provider = provider or os.getenv("SPATIAL_PROVIDER") or ("deepseek" if os.getenv("DEEPSEEK_API_KEY") else "openai")
    from .deepseek_model import model_context
    with model_context(model) if chosen_provider == "deepseek" and model else nullcontext():
        result = await asyncio.wait_for(tool.ainvoke(payload_for(tool, message), config=config), timeout=timeout)
    return {"module": key, "provider": provider or os.getenv("SPATIAL_PROVIDER") or "auto",
            "model": model, "elapsed_seconds": round(time.monotonic() - started, 3),
            "message": message, "output": as_text(result),
            "tool_trace": events,
            "trace_scope": "tool entry/return, no arguments or results; return does not prove data correctness",
            "scope": "upstream_agent_output_not_verified_metadata"}
