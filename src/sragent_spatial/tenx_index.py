"""Discover official 10x data cards via a separate public web index."""

import json
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .deepseek_model import api_key
from .research_request import tissue_matches
from .tenx_online import CATALOG, CatalogAccessError, platform_for

SEARCH = "https://api.deepseek.com/v1/tools/search"
MODE = "indexed_official_10x_cards"
FIELDS = re.compile(r"^(Species|Anatomical Entity|Disease State|Product|Date Published)\s*:?\s*(?:\n+)?([^\n]+)$", re.M)
OTHER_SPECIES = re.compile(r"\b(?:mouse|mice|rat|murine|non[- ]human|xenograft|zebrafish|macaque)\b", re.I)
KNOWN_ARCHIVES = {
    "visium-hd-cytassist-6p5mm-human-heart": ("4.1.0", "Visium_HD_6p5mm_Human_Heart", "2026-11-04"),
    "visium-hd-three-prime-human-tonsil-fresh-frozen": ("4.1.0", "Visium_HD_3prime_Human_Tonsil", "2026-11-04"),
    "visium-hd-cytassist-gene-expression-libraries-human-pancreas": ("3.0.0", "Visium_HD_Human_Pancreas", "2026-11-04"),
    "visium-hd-cytassist-gene-expression-libraries-human-kidney-ffpe": ("3.1.2", "Visium_HD_Human_Kidney_FFPE", "2026-11-04"),
}
TISSUE_TERMS = {"ovary": "ovarian", "kidney": "renal", "heart": "cardiac", "liver": "hepatic",
                "lung": "pulmonary", "pancreas": "pancreatic", "uterus": "uterine", "prostate": "prostatic"}


def lookup_public_cards(query, count):
    key = api_key()
    if not key:
        raise CatalogAccessError("Public index requires a configured DeepSeek Search key")
    import httpx
    from .deepseek_model import native_tls_context
    try:
        response = httpx.post(SEARCH, headers={"Authorization": "Bearer " + key},
            json={"query": query, "count": count, "need_content": True}, timeout=35,
            verify=native_tls_context() if sys.platform == "win32" else True)
    except httpx.RequestError as exc:
        raise CatalogAccessError("Public web index request failed: " + type(exc).__name__) from exc
    if response.status_code != 200:
        raise CatalogAccessError(f"DeepSeek public index returned HTTP {response.status_code}")
    if len(response.content) > 5 * 1024**2:
        raise CatalogAccessError("Public index response exceeded the size limit")
    return response.json()["results"]


def card_row(result, tissue, platform):
    url = urlsplit(result.get("url") or "")
    path = re.fullmatch(r"/(?:(?:cn|jp)/)?datasets/([A-Za-z0-9_-]+)", url.path)
    if not path or url.scheme != "https" or url.netloc != "www.10xgenomics.com":
        return None
    title = result.get("title", "").split(" | 10x Genomics")[0].strip()
    body = (result.get("content") or "")[:40000]
    fields = {key: value.strip() for key,value in FIELDS.findall(body)}
    if not body or OTHER_SPECIES.search(title) or re.search(r"\btissue (?:micro)?array\b", title, re.I):
        return None
    species = fields.get("Species")
    if (species and species.casefold() != "human") or (not species and not re.search(r"\bhuman\b", title, re.I)):
        return None
    product = fields.get("Product", "")
    identified = platform_for(product, title)
    if identified is None and product == "Spatial Gene Expression" and re.search(r"\bvisium\b", body, re.I):
        identified = "Visium"
    if identified != platform:
        return None
    anatomy = fields.get("Anatomical Entity")
    if anatomy and re.search(r"[,;/]", anatomy):
        return None
    if tissue != "*" and not (
            tissue_matches(tissue, anatomy or title)
            or re.search(r"\b"+re.escape(TISSUE_TERMS.get(tissue.casefold(),tissue))+r"\b", anatomy or title, re.I)):
        return None
    if not anatomy and tissue == "*":
        return None
    slug = path[1]
    row = {"sample_id": slug, "source_url": f"{CATALOG}/{slug}", "title": title,
           "species": "Human", "tissue": anatomy or tissue, "platform": platform, "catalog_mode": MODE,
           "indexed_url": result["url"], "archives": {}, "metadata_evidence": fields}
    disease = fields.get("Disease State")
    if disease:
        row["disease"] = disease
        if disease.casefold() in {"healthy", "normal", "non-diseased", "non diseased", "non-diseased tissue"}:
            row["state"] = "healthy"
    if platform == "Visium HD" and re.search(r"Number of samples on this datasets page\s+1\b", body, re.I):
        links = sorted(set(re.findall(
            r"https://cf\.10xgenomics\.com/samples/spatial-exp/[\w.-]+/[\w-]+/[\w-]+_binned_outputs\.tar\.gz(?![\w.?#])", body)))
        if links:
            row["archives"] = {"binned_outputs": links}
            row["asset_evidence"] = {"mode": "explicit_link_in_indexed_publisher_card"}
    if not row["archives"] and slug in KNOWN_ARCHIVES and platform == "Visium HD":
        version, name, date = KNOWN_ARCHIVES[slug]
        row["archives"] = {"binned_outputs": [f"https://cf.10xgenomics.com/samples/spatial-exp/{version}/{name}/{name}_binned_outputs.tar.gz"]}
        row["asset_evidence"] = {"mode": "previously_verified_official_download_link", "recorded_on": date}
    return row


def discover_indexed_human(tissue, platform, limit, *, disease=""):
    query = "site:10xgenomics.com/datasets Human " + platform + " " + ("" if tissue == "*" else tissue)
    if disease:
        query += " " + disease
    results = lookup_public_cards(query[:128].strip(), max(8,limit*4))
    rows = {}
    for result in results:
        row = card_row(result,tissue,platform)
        if row:
            rows.setdefault(row["sample_id"],row)
    return list(rows.values())[:limit]


def official_catalog_status():
    try:
        with urlopen(Request(CATALOG, headers={"User-Agent":"SRAgent-spatial/0.16 public dataset metadata"}), timeout=12) as response:
            return {"source_url":CATALOG,"status":"http_reachable","http_status":response.status}
    except OSError as exc:
        status = getattr(exc, "code", None)
        headers = getattr(exc, "headers", None) or {}
        return {"source_url":CATALOG,"status":"access_failed","http_status":status,
                "browser_challenge":headers.get("x-vercel-mitigated")=="challenge"}
