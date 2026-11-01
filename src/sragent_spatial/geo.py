"""Read public GEO metadata, with cache, bounded responses and verified TLS."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ACCESSION = re.compile(r"(?:GSE|GSM)[1-9][0-9]*\Z")
MAX_RESPONSE = 8 * 1024 * 1024
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def checked_accession(value: str) -> str:
    value = value.strip().upper()
    if not ACCESSION.fullmatch(value):
        raise ValueError("Expected a GEO GSE or GSM accession, e.g. GSE250346")
    return value


def source_url(accession: str) -> str:
    return "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?" + urlencode({"acc": checked_accession(accession)})


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class MetadataClient:
    def __init__(self, cache: Path, *, offline=False, transport="auto", retries=2, refresh=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.transport = transport
        self.retries = retries
        self.refresh = refresh
        if offline and refresh:
            raise ValueError("--offline and --refresh cannot be combined")
        self.requests = []
        self.last_request = 0.0

    def _powershell(self, url: str) -> bytes:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if os.name != "nt" or not executable:
            raise RuntimeError("PowerShell transport requires Windows and PowerShell")
        # Static script; the generated NCBI URL is passed as data, never shell code.
        script = (
            "$ErrorActionPreference='Stop';"
            "$r=Invoke-WebRequest -Uri $env:SRAGENT_SPATIAL_METADATA_URL "
            "-UseBasicParsing -TimeoutSec 30;"
            "[Convert]::ToBase64String($r.RawContentStream.ToArray())"
        )
        env = dict(os.environ, SRAGENT_SPATIAL_METADATA_URL=url)
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, timeout=45, check=False,
        )
        if result.returncode:
            raise RuntimeError("System HTTPS metadata request failed: " + result.stderr.decode("utf-8", errors="replace")[:600])
        return base64.b64decode(result.stdout.strip(), validate=True)

    def _download(self, url: str) -> tuple[bytes, str]:
        if self.transport == "powershell":
            return self._powershell(url), "powershell"
        try:
            request = Request(url, headers={"User-Agent": "SRAgent-spatial/0.1 (public metadata lookup)"})
            with urlopen(request, timeout=30) as response:
                return response.read(MAX_RESPONSE + 1), "urllib"
        except URLError as exc:
            if (self.transport == "auto" and os.name == "nt"
                    and isinstance(exc.reason, ssl.SSLCertVerificationError)):
                # Use Windows certificate validation; never disable TLS verification.
                return self._powershell(url), "powershell"
            raise

    def get(self, url: str) -> bytes:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in {"www.ncbi.nlm.nih.gov", "eutils.ncbi.nlm.nih.gov"}:
            raise ValueError("Metadata client accepts only fixed HTTPS NCBI endpoints")
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".bin")
        meta_path = self.cache / (key + ".json")
        if not self.refresh and path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = path.read_bytes()
            if len(body) > MAX_RESPONSE or hashlib.sha256(body).hexdigest() != meta["sha256"]:
                raise ValueError("Cached response failed size/checksum validation")
            self.requests.append({**meta, "cached": True})
            return body
        if self.offline:
            raise RuntimeError("No cached response for " + url)
        for attempt in range(self.retries + 1):
            time.sleep(max(0, 0.4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                body, used_transport = self._download(url)
                if not body or len(body) > MAX_RESPONSE:
                    raise ValueError("Empty or oversized metadata response")
                start = body.lstrip(b"\xef\xbb\xbf \t\r\n")
                if parts.path.endswith("acc.cgi") and not start.startswith((b"^SERIES", b"^SAMPLE")):
                    raise ValueError("GEO returned a non-SOFT response (possibly an error or access page)")
                meta = {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "sha256": hashlib.sha256(body).hexdigest(), "transport": used_transport}
                path.write_bytes(body)
                write_json(meta_path, meta)
                self.requests.append({**meta, "cached": False})
                return body
            except (HTTPError, URLError, TimeoutError, RuntimeError, subprocess.TimeoutExpired):
                if attempt == self.retries:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("Request attempts exhausted")

    def soft(self, accession: str) -> str:
        query = urlencode({"acc": checked_accession(accession), "targ": "self", "form": "text", "view": "full"})
        return self.get("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?" + query).decode("utf-8-sig")

    def get_json(self, endpoint: str, **params):
        if endpoint not in {"esearch.fcgi", "esummary.fcgi"}:
            raise ValueError("Unsupported EUtils endpoint")
        result = json.loads(self.get(EUTILS + endpoint + "?" + urlencode(params)))
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        return result


def parse_soft(text: str, accession: str) -> dict[str, list[str]]:
    """Read only the requested record, retaining repeated SOFT fields."""
    accession = checked_accession(accession)
    fields = {}
    active = False
    found = False
    for line in text.splitlines():
        if line.startswith("^"):
            active = line.partition(" = ")[2].strip() == accession
            found |= active
        elif active and line.startswith("!") and " = " in line:
            key, _, value = line[1:].partition(" = ")
            fields.setdefault(key, []).append(value.strip())
    if not found:
        raise ValueError("GEO response did not contain the requested SOFT record: " + accession)
    return fields


def asset_kind(url: str) -> str:
    name = urlsplit(url).path.lower()
    if any(x in name for x in ["annotation", "geojson", "gene_groups"]):
        return "annotation"
    if any(x in name for x in ["segmentation", "boundaries"]):
        return "segmentation"
    if any(x in name for x in ["tissue_positions", "spatial", "coordinates"]):
        return "coordinates" if name.endswith((".csv", ".csv.gz", ".json")) else "other"
    if any(x in name for x in [".tif", ".svs", ".jpg", ".png"]):
        return "image"
    if any(x in name for x in ["matrix", "expression", ".h5ad", "counts"]):
        return "expression"
    if "alignment" in name or "transform" in name:
        return "transform"
    return "other"


def record_from_soft(fields: dict, accession: str) -> dict:
    prefix = "Series" if accession.startswith("GSE") else "Sample"
    url = source_url(accession)
    evidence = []

    def values(suffix):
        return fields.get(prefix + "_" + suffix, [])

    def field_value(name, suffix):
        vals = list(dict.fromkeys(v for v in values(suffix) if v))
        for val in vals:
            evidence.append({"field": name, "source_url": url,
                             "description": f"!{prefix}_{suffix} = {val}", "method": "source_explicit"})
        return "; ".join(vals) or None

    title = field_value("title", "title") or ""
    summary = field_value("summary", "summary")
    organism = field_value("organism", "organism_ch1")
    characteristics = values("characteristics_ch1")

    def characteristic(name, keys):
        vals = []
        for item in characteristics:
            key, sep, val = item.partition(":")
            if sep and key.strip().lower() in keys and val.strip():
                vals.append(val.strip())
                evidence.append({"field": name, "source_url": url,
                                 "description": item, "method": "source_explicit"})
        return "; ".join(dict.fromkeys(vals)) or None

    tissue = characteristic("tissue", {"tissue", "tissue type", "organ", "organism part"})
    disease = characteristic("disease", {"disease", "disease state", "disease status", "diagnosis"})
    # Search only descriptive/assay fields, not contacts, file names or related studies.
    descriptive = [v for k, vals in fields.items() if any(
        token in k.lower() for token in ["title", "summary", "design", "protocol", "description", "characteristics"]
    ) for v in vals]
    platforms = []
    for name in ["Visium", "Xenium", "MERFISH", "CosMx", "Stereo-seq", "Slide-seq"]:
        hits = [v for v in descriptive if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", v, re.I)]
        if hits:
            platforms.append(name)
            evidence.append({"field": "platform", "source_url": url,
                             "description": hits[0], "method": "inferred"})
    platform = platforms[0] if len(platforms) == 1 else ("mixed_or_ambiguous" if platforms else "unknown")
    assets = []
    for key, vals in fields.items():
        if key.startswith(prefix + "_supplementary_file"):
            for value in vals:
                if urlsplit(value).scheme in {"http", "https", "ftp"} and value not in [x["url"] for x in assets]:
                    assets.append({"kind": asset_kind(value), "url": value, "section_id": None,
                                   "notes": "Type inferred from filename; link not downloaded or spatially verified."})
    related = list(dict.fromkeys(values("sample_id") + values("series_id")))
    return {
        "record_id": "GEO:" + accession, "source_database": "GEO", "source_accession": accession,
        "source_url": url, "retrieved_at": datetime.now(timezone.utc).date().isoformat(),
        "title": title, "summary": summary, "organism": organism, "tissue": tissue, "disease": disease,
        "platform": platform, "platform_candidates": platforms, "observation_unit": "unknown",
        "study_id": accession if prefix == "Series" else (related[0] if len(related) == 1 else None),
        "sample_id": accession if prefix == "Sample" else None, "section_id": None,
        "coordinate_unit": None, "coordinate_system": None, "status": "candidate",
        "related_accessions": related, "assets": assets, "evidence": evidence,
    }


def lookup(client: MetadataClient, accession: str, max_samples=0) -> tuple[list[dict], dict]:
    if not 0 <= max_samples <= 20:
        raise ValueError("max_samples must be in 0..20")
    accession = checked_accession(accession)
    root = record_from_soft(parse_soft(client.soft(accession), accession), accession)
    records = [root]
    samples = [x for x in root["related_accessions"] if x.startswith("GSM")] if accession.startswith("GSE") else []
    for child in samples[:max_samples]:
        records.append(record_from_soft(parse_soft(client.soft(child), child), child))
    report = {"accession": accession, "sample_records_available": len(samples),
              "sample_records_fetched": min(len(samples), max_samples),
              "all_sample_records_fetched": len(samples) <= max_samples,
              "note": "Series fields are never propagated as sample disease/platform/coordinates."}
    return records, report


def build_query(organism: str, tissue: str, platform: str) -> str:
    for value in (organism, tissue, platform):
        if not value.strip() or re.search(r'["\[\]\r\n]', value):
            raise ValueError("Search values must be nonempty and contain no quotes or field operators")
    return f'"{organism}"[Organism] AND "{tissue}"[All Fields] AND "{platform}"[All Fields] AND gse[ETYP]'


def search(client: MetadataClient, organism: str, tissue: str, platform: str, limit=5):
    if not 1 <= limit <= 20:
        raise ValueError("limit must be in 1..20")
    query = build_query(organism, tissue, platform)
    result = client.get_json("esearch.fcgi", db="gds", term=query, retmode="json", retmax=limit)
    found = result["esearchresult"]
    if found.get("errorlist"):
        raise ValueError("GEO rejected search fields: " + json.dumps(found["errorlist"]))
    ids = found.get("idlist", [])
    records = []
    skipped = []
    if ids:
        details = client.get_json("esummary.fcgi", db="gds", id=",".join(ids), retmode="json")["result"]
        for uid in ids:
            accession = details.get(uid, {}).get("accession", "")
            if not re.fullmatch(r"GSE[1-9][0-9]*", accession):
                skipped.append(uid)
                continue
            recs, _ = lookup(client, accession)
            records.extend(recs)
    return records, {"query": query, "total_hits": int(found.get("count", 0)),
                     "returned_ids": ids, "skipped_nonseries_ids": skipped,
                     "warnings": found.get("warninglist", {}), "records_exported": len(records)}


def export_records(output: Path, records: list[dict], report: dict, client: MetadataClient):
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "records.json", records)
    write_json(output / "run.json", {**report, "requests": client.requests})
    columns = ["record_id", "source_database", "source_accession", "source_url", "retrieved_at", "title",
               "organism", "tissue", "disease", "platform", "observation_unit", "study_id", "sample_id", "status"]
    with (output / "records.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = {key: record.get(key) for key in columns}
            # Keep source text literal in JSON, protect spreadsheet opening of CSV.
            row = {key: ("'" + val if isinstance(val, str) and val.startswith(("=", "+", "-", "@")) else val)
                   for key, val in row.items()}
            writer.writerow(row)
