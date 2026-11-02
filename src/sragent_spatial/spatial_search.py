"""Spatial dataset discovery: platform catalog + Entrez query builder + search.

Unlike upstream ``esearch_scrna`` (which hard-codes 10x/paired/Illumina and excludes
Smart-seq/MARS-seq), spatial discovery is keyed on the assay name itself. Imaging
platforms (Xenium/MERFISH/CosMx) are decoded by the vendor and cannot be "re-processed"
from raw reads, while sequencing platforms (Visium/Slide-seq/Stereo-seq) can. That
distinction is recorded here and used later for asset pairing.

This module stays standard-library only; a LangChain tool wrapper is provided via
``as_langchain_tool()`` so the agent layer can call it without importing langchain here.
"""

from __future__ import annotations

import re

#: key -> catalog entry. ``terms`` are what we search in Entrez ``[All Fields]``.
SPATIAL_PLATFORMS: dict[str, dict] = {
    "Visium": {"terms": ["Visium", "10x Visium", "Visium Spatial Gene Expression"],
               "kind": "sequencing", "resolution": "spot"},
    "Visium HD": {"terms": ["Visium HD", "VisiumHD"],
                  "kind": "sequencing", "resolution": "bin"},
    "Xenium": {"terms": ["Xenium"],
               "kind": "imaging", "resolution": "cell"},
    "MERFISH": {"terms": ["MERFISH", "MERSCOPE"],
                "kind": "imaging", "resolution": "cell"},
    "CosMx": {"terms": ["CosMx", "CosMx SMI"],
              "kind": "imaging", "resolution": "cell"},
    "Stereo-seq": {"terms": ["Stereo-seq", "StereoSeq"],
                   "kind": "sequencing", "resolution": "bin"},
    "Slide-seq": {"terms": ["Slide-seq", "Slide-seqV2"],
                  "kind": "sequencing", "resolution": "bead"},
}

PLATFORM_KEYS = tuple(SPATIAL_PLATFORMS)

#: assay signals that must NOT dominate the query result as a false positive.
NEGATIVE_TERMS = ("bulk RNA-seq", "scATAC-seq", "ATAC-seq", "CITE-seq")


def _clean(value: str) -> str:
    return value.strip()


def check_text(value: str, label: str) -> str:
    value = _clean(value)
    if not value or re.search(r'["\[\]\r\n]', value):
        raise ValueError(f"{label} must be nonempty and contain no quotes or field operators")
    return value


def check_platforms(platforms) -> list[str]:
    if isinstance(platforms, str):
        platforms = [platforms]
    keys = []
    for name in platforms:
        key = _clean(name)
        if key not in SPATIAL_PLATFORMS:
            raise ValueError(f"Unknown spatial platform {name!r}; expected one of {', '.join(PLATFORM_KEYS)}")
        if key not in keys:
            keys.append(key)
    if not keys:
        raise ValueError("At least one spatial platform is required")
    return keys


def build_spatial_query(organism: str, tissue: str, platforms, *, series_only=True) -> str:
    """Entrez GDS query for spatial datasets.

    Only descriptive fields are constrained; we never filter on read layout or sequencer,
    because imaging platforms have neither.
    """
    parts = [f'"{check_text(organism, "organism")}"[Organism]',
             f'"{check_text(tissue, "tissue")}"[All Fields]']
    terms = []
    for key in check_platforms(platforms):
        terms.extend(SPATIAL_PLATFORMS[key]["terms"])
    parts.append("(" + " OR ".join(f'"{term}"[All Fields]' for term in terms) + ")")
    if series_only:
        parts.append("gse[ETYP]")
    return " AND ".join(parts)


def platform_kinds(platforms) -> dict[str, str]:
    return {key: SPATIAL_PLATFORMS[key]["kind"] for key in check_platforms(platforms)}


def spatial_search(client, organism: str, tissue: str, platforms, limit: int = 5, *, continue_on_error=False):
    """Search GEO for spatial series and hydrate records via the shared lookup path."""
    from .geo import lookup

    if not 1 <= limit <= 20:
        raise ValueError("limit must be in 1..20")
    query = build_spatial_query(organism, tissue, platforms)
    found = client.get_json("esearch.fcgi", db="gds", term=query, retmode="json", retmax=limit)
    if found["esearchresult"].get("errorlist"):
        raise ValueError("GEO rejected search fields: " + ", ".join(found["esearchresult"]["errorlist"]))
    ids = found["esearchresult"].get("idlist", [])
    details = client.get_json("esummary.fcgi", db="gds", id=",".join(ids), retmode="json")["result"] if ids else {}
    records = []
    skipped = []
    failures = []
    for uid in ids:
        accession = details.get(uid, {}).get("accession", "")
        if not re.fullmatch(r"GSE[1-9][0-9]*", accession):
            skipped.append(uid)
            continue
        try:
            records.extend(lookup(client, accession)[0])
        except Exception as exc:
            if not continue_on_error:
                raise
            failures.append({"accession": accession, "error_type": type(exc).__name__})
    return records, {"query": query, "platforms": check_platforms(platforms),
                     "platform_kinds": platform_kinds(platforms),
                     "total_hits": int(found["esearchresult"].get("count", 0)),
                     "returned_ids": ids, "skipped_nonseries_ids": skipped,
                     "records_exported": len(records), "study_failures": failures,
                     "search_truncated": int(found["esearchresult"].get("count", 0)) > len(ids)}


def as_langchain_tool():
    """Optional ``@tool`` wrapper for use inside a LangGraph agent (imports langchain lazily)."""
    from langchain_core.tools import tool

    @tool
    def search_spatial_datasets(
        organism: str,
        tissue: str,
        platforms: list[str],
    ) -> str:
        """Search GEO for spatial transcriptomics series by organism, tissue and platform(s).

        platforms are keys like Visium, Visium HD, Xenium, MERFISH, CosMx, Stereo-seq, Slide-seq.
        Returns a JSON list of candidate records with source URLs and platform evidence.
        """
        import json

        from .geo import MetadataClient

        client = MetadataClient(cache="outputs/cache")
        records, report = spatial_search(client, organism, tissue, platforms)
        return json.dumps({"report": report, "records": records}, ensure_ascii=False, default=str)

    return search_spatial_datasets
