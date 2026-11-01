"""Exercise actual installed SRAgent/LangGraph with a mocked LLM and blocked HTTP."""

import asyncio
import json
import os
import sys
from importlib import metadata, resources
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


async def main():
    if os.name == "nt":
        import truststore
        truststore.inject_into_ssl()
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from sragent_spatial.agents import predict_agent
    from sragent_spatial.screening import screen_sample
    print("Loading installed upstream model factory", flush=True)
    from SRAgent.agents.utils import set_model
    print("Installed upstream imports ready", flush=True)

    packet = {"accession": "GSM999001", "source_url": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM999001",
              "fields": {"Sample_organism_ch1": ["Homo sapiens"], "Sample_characteristics_ch1": ["tissue: lung"],
                         "Sample_extract_protocol_ch1": ["Fictional Visium assay for offline testing"]}}
    calls = []

    async def fake_generate(self, messages, *args, **kwargs):
        calls.append(type(self).__name__)
        system = str(messages[0].content)
        if "Use sample-level tools" in system and not any(msg.type == "tool" for msg in messages):
            message = AIMessage(content="", tool_calls=[
                {"name": "read_spatial_sample", "args": {"accession": packet["accession"]}, "id": "offline-read", "type": "tool_call"},
                {"name": "screen_spatial_sample", "args": {"accession": packet["accession"]}, "id": "offline-screen", "type": "tool_call"},
                {"name": "invoke_ncbi_fetch_agent", "args": {"message": "Use the supplied fictional GSM999001 sample packet only."},
                 "id": "offline-upstream", "type": "tool_call"}
            ])
        else:
            message = AIMessage(content=json.dumps(screen_sample(packet)))
        return ChatResult(generations=[ChatGeneration(message=message)])

    settings = str(resources.files("sragent_spatial").joinpath("agent_settings.yml"))
    env = {"OPENAI_API_KEY": "offline-construction-placeholder", "DYNACONF_SETTINGS_PATH": settings,
           "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
    with patch.dict(os.environ, env), patch("langchain_openai.ChatOpenAI._agenerate", new=fake_generate), \
            patch("httpx.AsyncClient.send", side_effect=RuntimeError("Unexpected HTTP in offline integration check")):
        results = []
        for method in ("original_agent", "spatial_agent"):
            print("Checking " + method, flush=True)
            results.append(await predict_agent(packet, method, "gpt-5-mini", timeout=30))
    if not all(r["label"] == "eligible" for r in results) or len(calls) != 4:
        raise RuntimeError("Expected original, spatial tools, nested upstream and final generations")
    print(json.dumps({"upstream_version": metadata.version("SRAgent"), "methods_checked": [r["method"] for r in results],
                      "mocked_model_generations": len(calls), "provider_requests_sent": False,
                      "note": "Actual framework and frozen tool execution, mocked model output; not model-quality validation"}))


if __name__ == "__main__":
    asyncio.run(main())
