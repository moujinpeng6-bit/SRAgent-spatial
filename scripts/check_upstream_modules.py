"""Real factories/schemas/wrappers for all modules, plus nested supervisor dispatch.

Models and external tool responses are mocked; HTTP is blocked. This is interface
verification, not evidence that BigQuery credentials or ontology resources work.
"""

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def check():
    import truststore
    truststore.inject_into_ssl()
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.graph.state import CompiledStateGraph
    from sragent_spatial import upstream_registry as registry
    from sragent_spatial.agents import installed_upstream

    if not installed_upstream()["matches_required_commit"]:
        raise RuntimeError("Install the pinned upstream before checking its interfaces")
    rows = []
    current = ""

    async def fake_graph(self, input, *args, **kwargs):
        if not input.get("messages"):
            raise AssertionError("Wrapper did not supply graph messages")
        response = {"messages": [AIMessage(content="offline wrapper response")]}
        if current == "papers":
            cls = importlib.import_module("SRAgent.agents.papers").PublicationsResult
            response["structured_response"] = cls(accession="SRX4967527", publications=[])
        else:
            response["structured_response"] = SimpleNamespace(id="UBERON:0002048")
        return response

    with patch("httpx.AsyncClient.send", side_effect=AssertionError("HTTP blocked")), \
            patch("httpx.Client.send", side_effect=AssertionError("HTTP blocked")), \
            patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP blocked")):
        with patch.object(CompiledStateGraph, "ainvoke", new=fake_graph), \
                patch("google.cloud.bigquery.Client", return_value=object()):
            for module in registry.MODULES:
                current = module.key
                try:
                    tool = registry.load(module.key, "deepseek-flash", provider="deepseek")
                    payload = registry.payload_for(tool, "SRX4967527" if current == "papers" else "offline input")
                    tool.get_input_schema().model_validate(payload)
                    await tool.ainvoke(payload, config={"recursion_limit": 8})
                    row = {"module": current, "status": "passed", "input_fields": sorted(payload)}
                except Exception as exc:
                    row = {"module": current, "status": "failed", "error_type": type(exc).__name__}
                rows.append(row)
                print(json.dumps(row), flush=True)

        generations = []

        async def generate(self, messages, *args, **kwargs):
            names = {entry["function"]["name"] for entry in kwargs.get("tools", [])}
            if messages[-1].type == "tool":
                message = AIMessage(content="Offline nested result")
            else:
                if "invoke_entrez_agent" in names:
                    name, arguments = "invoke_entrez_agent", {"message": "Use esummary for offline record"}
                elif "invoke_esummary_agent" in names:
                    name, arguments = "invoke_esummary_agent", {"message": "Summarize offline record"}
                elif "esummary" in names:
                    name, arguments = "esummary", {"ids": ["12345"], "database": "gds"}
                    schema = next(t for t in kwargs["tools"] if t["function"]["name"] == name)["function"]["parameters"]
                    arguments = {key: (["12345"] if prop.get("type") == "array" else "gds" if "database" in key else "12345")
                                 for key, prop in schema["properties"].items() if key in schema.get("required", [])}
                else:
                    raise AssertionError("Unexpected nested tool set")
                message = AIMessage(content="", tool_calls=[{"name": name, "args": arguments,
                                     "id": "offline-" + name, "type": "tool_call"}])
            generations.append(self.model_name)
            return ChatResult(generations=[ChatGeneration(message=message)])

        leaf = importlib.import_module("SRAgent.tools.esummary").esummary
        with patch("langchain_openai.ChatOpenAI._agenerate", new=generate), \
                patch.object(leaf, "func", return_value="Offline esummary metadata"):
            result = await registry.invoke("sragent", "Use Entrez and esummary for this offline check",
                                           "deepseek-flash", provider="deepseek", timeout=30)
        names = [event["name"] for event in result["tool_trace"] if event["event"] == "start"]
        expected = {"invoke_sragent_agent", "invoke_entrez_agent", "invoke_esummary_agent", "esummary"}
        if not expected <= set(names):
            raise AssertionError("Missing nested dispatch: " + str(names))
        report = {"modules": rows, "nested_supervisor": {"status": "passed", "tools_entered": names,
                  "mocked_generations": len(generations), "model_ids": sorted(set(generations))},
                  "provider_requests_sent": False,
                  "scope": "Real installed factories, schemas, wrappers and nested dispatch; mocked models/graph or leaf responses"}
        target = ROOT / "outputs" / "module_validation" / "offline_interfaces.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if any(row["status"] != "passed" for row in rows):
            raise RuntimeError("Some module interfaces failed; inspect offline_interfaces.json")
        print(json.dumps(report["nested_supervisor"]), flush=True)


if __name__ == "__main__":
    from importlib import resources
    settings = ROOT / "src" / "sragent_spatial" / "agent_settings.yml"
    with patch.dict(os.environ, {"SPATIAL_PROVIDER": "deepseek", "SPATIAL_MODEL": "deepseek-flash",
                                "DEEPSEEK_API_KEY": "offline-placeholder", "LANGSMITH_TRACING": "false",
                                "LANGCHAIN_TRACING_V2": "false", "DYNACONF_SETTINGS_PATH": str(settings)}):
        asyncio.run(check())
