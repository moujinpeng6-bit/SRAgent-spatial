"""Conservative, sample-level human lung spatial screening with field evidence."""

from __future__ import annotations

import hashlib
import json
import re

LABELS = ("eligible", "non_lung", "out_of_scope", "mixed", "unknown")
PLATFORMS = ("Visium", "Xenium", "MERFISH", "CosMx", "Stereo-seq", "Slide-seq")
VISIUM_HD = re.compile(r"\bvisium\s*(?:cytassist\s+)?hd\b", re.I)
LUNG = re.compile(r"\blungs?\b|肺", re.I)
NON_LUNG = re.compile(
    r"ventricl|ventricle|\bheart\b|myocard|larynx|hypopharynx|\bliver\b|\bkidney\b|\bbrain\b|"
    r"\bbreast\b|pancrea|\bcolon\b|ovary|\bskin\b|lymph node|tonsil|\bbone\b|uterus|"
    r"prostate|adrenal|esophagus|trachea|spleen|心脏|肝脏|肾脏", re.I
)


def validate_packet(packet: dict) -> None:
    accession = packet.get("accession", "")
    if not re.fullmatch(r"GSM[1-9][0-9]*", accession):
        raise ValueError("Screening requires a GSM sample, not a study")
    fields = packet.get("fields")
    if not isinstance(fields, dict) or not all(
        isinstance(k, str) and k.startswith("Sample_") and isinstance(v, list)
        and all(isinstance(x, str) for x in v) for k, v in fields.items()
    ):
        raise ValueError("Sample field map must contain string lists")
    from .geo import source_url
    if packet.get("source_url") != source_url(accession):
        raise ValueError("Evidence URL must identify the requested GSM")
    if packet.get("fields_sha256"):
        digest = hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != packet["fields_sha256"]:
            raise ValueError("Sample evidence checksum mismatch")


def field_entries(packet: dict):
    for key, values in packet["fields"].items():
        for value in values:
            yield {"field": key, "quote": value, "source_url": packet["source_url"]}


def screen_sample(packet: dict) -> dict:
    validate_packet(packet)
    entries = list(field_entries(packet))
    tissue_entries = []
    for item in entries:
        if item["field"].startswith("Sample_characteristics"):
            key, sep, value = item["quote"].partition(":")
            if sep and key.strip().lower() in {"tissue", "organ", "organism part", "tissue type"}:
                tissue_entries.append((value.strip(), item))
    if not tissue_entries:
        tissue_entries = [(item["quote"], item) for item in entries
                          if item["field"].startswith("Sample_source_name")]
    tissue = "; ".join(value for value, _ in tissue_entries)
    organ_entries = [item for item in entries if item["field"].startswith("Sample_organism")]
    organisms = {item["quote"].strip().lower() for item in organ_entries}
    assay_entries = [item for item in entries if any(name in item["field"] for name in
                     ("title", "extract_protocol", "description", "characteristics", "library_"))]
    platform_hits = {}
    for platform in PLATFORMS:
        for item in assay_entries:
            if (platform == "Visium" and VISIUM_HD.search(item["quote"])) or re.search(
                    r"(?<!\w)" + re.escape(platform) + r"(?!\w)", item["quote"], re.I):
                platform_hits[platform] = item
                break
    lung = bool(LUNG.search(tissue)) and not bool(re.search(r"non[- ]lung|not lung", tissue, re.I))
    other = bool(NON_LUNG.search(tissue))
    citations = [item for _, item in tissue_entries] + organ_entries + list(platform_hits.values())
    citations = list({json.dumps(item, sort_keys=True): item for item in citations}.values())
    scope = packet.get("screen_scope")
    if scope:
        organism, target = scope["organism"].strip().casefold(), scope["tissue"].strip()
        species_match = organism == "*" or organism in organisms or (
            organism == "homo sapiens" and "human" in organisms)
        tissue_match = target == "*" or any(
            re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", value, re.I)
            and not re.search(r"\b(?:non[- ]|not +)" + re.escape(target) + r"(?!\w)", value, re.I)
            for value, _ in tissue_entries)
        if len(platform_hits) > 1 or len(organisms) > 1:
            label, reason = "mixed", "Multiple sample platform or organism signals require review."
        elif organisms and not species_match:
            label, reason = "out_of_scope", "Sample organism differs from the requested organism."
        elif organisms and species_match and tissue_match and len(platform_hits) == 1:
            label, reason = "eligible", "Sample metadata matches the requested scope and has a spatial assay signal."
        else:
            label, reason = "unknown", "Could not confirm the requested sample scope and a spatial assay."
    elif (lung and other) or len(platform_hits) > 1 or len(organisms) > 1:
        label, reason = "mixed", "Multiple organ, platform or organism signals require review."
    elif other and not lung:
        label, reason = "non_lung", "Explicit sample tissue/source identifies a non-lung organ."
    elif lung and organisms <= {"homo sapiens", "human"} and organisms and len(platform_hits) == 1:
        label, reason = "eligible", "Explicit human lung sample with a spatial platform mention; assets not verified."
    else:
        label, reason = "unknown", "Insufficient or unsupported sample-level organ/species/platform evidence."
    return {"accession": packet["accession"], "label": label, "reason": reason,
            "evidence": citations, "method": "rules-v0.2", "citation_validated": True,
            "tissue_text": tissue or None, "platform_candidates": list(platform_hits),
            "scope": "metadata_screen_only_not_spatial_asset_validation"}


def parse_prediction(text: str, packet: dict) -> dict:
    """Validate model output/citations; this does not validate semantic correctness."""
    validate_packet(packet)
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    result = json.loads(text)
    if not isinstance(result, dict) or result.get("accession") != packet["accession"]:
        raise ValueError("Model returned the wrong sample identity")
    if result.get("label") not in LABELS or not isinstance(result.get("reason"), str):
        raise ValueError("Model label or reason is invalid")
    evidence = result.get("evidence")
    if not isinstance(evidence, list) or (result["label"] != "unknown" and not evidence):
        raise ValueError("Non-unknown model labels require sample citations")
    for item in evidence:
        if not isinstance(item, dict) or item.get("source_url") != packet["source_url"]:
            raise ValueError("Model evidence must cite this exact sample")
        quote = item.get("quote")
        values = packet["fields"].get(item.get("field"), [])
        if not isinstance(quote, str) or len(quote.strip()) < 4 or not any(quote in value for value in values):
            raise ValueError("Model quote not found in the supplied sample field")
    return {"accession": result["accession"], "label": result["label"], "reason": result["reason"],
            "evidence": evidence, "citation_validated": True,
            "scope": "metadata_screen_only_not_spatial_asset_validation"}
