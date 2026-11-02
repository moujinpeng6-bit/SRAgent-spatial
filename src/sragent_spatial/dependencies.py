"""Local prerequisites for the unmodified pinned upstream tools; no network probes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def toolkit_bin():
    explicit = os.getenv("SRA_TOOLKIT_BIN")
    candidates = [Path(explicit)] if explicit else []
    candidates += sorted((ROOT / "outputs" / "tooling").glob("sratoolkit*/**/bin"))
    suffix = ".exe" if os.name == "nt" else ""
    for path in candidates:
        if all((path / (name + suffix)).is_file() for name in ("fastq-dump", "sra-stat")):
            return path
    return None


def activate_toolkit():
    """Expose a project-local install only to this process and its children."""
    path = toolkit_bin()
    if path and str(path) not in os.getenv("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(path) + os.pathsep + os.getenv("PATH", "")


def dependency_report():
    local = toolkit_bin()
    executables = {name: bool(shutil.which(name) or (local and (local / (name + (".exe" if os.name == "nt" else ""))).is_file()))
                   for name in ("fastq-dump", "sra-stat")}
    config = os.getenv("CLOUDSDK_CONFIG")
    if not config and os.getenv("APPDATA"):
        config = str(Path(os.environ["APPDATA"]) / "gcloud")
    if not config:
        try:
            config = str(Path.home() / ".config" / "gcloud")
        except RuntimeError:
            config = None
    adc = Path(config) / "application_default_credentials.json" if config else None
    credential_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credential_exists = Path(credential_file).is_file() if credential_file else bool(adc and adc.is_file())
    project = bool(os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"))
    try:
        import appdirs
        cache = Path(appdirs.user_cache_dir("SRAgent"))
    except ImportError:
        cache = None
    ontology = {}
    for key, store, graph in (("tissue_ontology", "uberon-full_chroma", "uberon-full.obo"),
                              ("disease_ontology", "mondo_chroma", "mondo.obo")):
        path = cache / store if cache else None
        store_exists = bool(path and path.is_dir() and any(path.iterdir()))
        graph_exists = bool(cache and (cache / graph).is_file())
        embedding_key = bool(os.getenv("OPENAI_API_KEY"))
        ontology[key] = {"vector_store_present": store_exists, "obo_graph_present": graph_exists,
                         "openai_embedding_key_present": embedding_key,
                         "embedding_model": "text-embedding-3-small",
                         "requirements_detected": store_exists and graph_exists and embedding_key,
                         "live_verified": False}
    return {"sequences": {"executables": executables, "requirements_detected": all(executables.values()),
                           "live_verified": False},
            "bigquery": {"project_configured": project, "credential_file_present": credential_exists,
                         "requirements_detected": project and credential_exists, "live_verified": False,
                         "note": "Presence only; credentials are not read/refreshed and cloud IAM/billing is not verified."},
            **ontology}

