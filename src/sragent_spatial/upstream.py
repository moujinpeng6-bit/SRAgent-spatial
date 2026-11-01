"""Optional bridge calling upstream SRAgent without copying its source."""

from .geo import checked_accession

UPSTREAM_COMMIT = "67b60c24a49385d3eed8e5121be58d13c82839cd"


async def agent_lookup(accession: str) -> dict:
    accession = checked_accession(accession)
    try:
        from SRAgent.agents.ncbi_fetch import create_ncbi_fetch_agent
    except ImportError as exc:
        raise RuntimeError("Optional upstream agent unavailable. Install '.[agent]' and configure the upstream model credentials.") from exc
    prompt = (
        f"Retrieve explicit public GEO metadata for {accession}. This is a spatial transcriptomics "
        "metadata task. Report tissue, organism, assay/platform, sample/study level, supplementary "
        "file URLs and source URLs. Distinguish Xenium, Visium and ordinary scRNA-seq. "
        "Do not assume every study sample has the disease mentioned in its title; "
        "mark absent coordinates, image registration or section identity as unknown. "
        "Do not infer spatial neighbors. Return source evidence and uncertainties."
    )
    agent = create_ncbi_fetch_agent()
    response = await agent.ainvoke({"message": prompt})
    messages = response.get("messages", [])
    return {"accession": accession, "upstream_commit": UPSTREAM_COMMIT,
            "review_status": "unreviewed_agent_output",
            "messages": [getattr(msg, "content", str(msg)) for msg in messages]}
