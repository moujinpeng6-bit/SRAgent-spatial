"""Public EMBL-EBI vector search for the two SRAgent ontology tools."""

import asyncio
import json
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://www.ebi.ac.uk/ols4/api"
MCP = API + "/mcp"
EMBEDDING_MODEL = "llama-embed-nemotron-8b_pca512"
MAX_RESPONSE = 4 * 1024**2


def decode_response(response, message_id):
    if "text/event-stream" in response.headers.get("Content-Type", ""):
        size = 0
        while line := response.readline(MAX_RESPONSE + 1):
            size += len(line)
            if size > MAX_RESPONSE:
                raise ValueError("OLS stream response exceeded the size limit")
            if line.startswith(b"data:"):
                result = json.loads(line[5:])
                if result.get("id") == message_id:
                    return result
        raise ValueError("OLS stream closed without the requested response")
    body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError("OLS response exceeded the size limit")
    return json.loads(body)


class OlsVectorClient:
    def __init__(self):
        self.headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.sequence = 0
        self.ready = False

    def rpc(self, method, params=None, *, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            self.sequence += 1
            payload["id"] = self.sequence
        with urlopen(Request(MCP, json.dumps(payload).encode(), self.headers), timeout=30) as response:
            if session := response.headers.get("Mcp-Session-Id"):
                self.headers["Mcp-Session-Id"] = session
            if notification:
                return None
            result = decode_response(response, self.sequence)
        if result.get("id") != self.sequence or "result" not in result:
            raise RuntimeError("OLS rejected " + method)
        return result["result"]

    def call(self, name, arguments):
        if not self.ready:
            info = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "sragent-spatial", "version": "0.16"}})
            self.headers["MCP-Protocol-Version"] = info["protocolVersion"]
            self.rpc("notifications/initialized", notification=True)
            models = self.rpc("tools/call", {"name": "listEmbeddingModels", "arguments": {}})
            if not any(m["model"] == EMBEDDING_MODEL and m["can_embed"] for m in tool_payload(models)):
                raise RuntimeError("OLS public embedding model is currently unavailable")
            self.ready = True
        return tool_payload(self.rpc("tools/call", {"name": name, "arguments": arguments}))


def tool_payload(result):
    if result.get("isError"):
        raise RuntimeError("OLS could not execute the requested tool")
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))


def term_id(ontology, value):
    return bool(re.fullmatch(ontology.upper() + r":\d{7}", value or ""))


def human_relevant(ontology, label):
    return ontology != "mondo" or not re.search(r"\b(?:non[- ]human|nonhuman animal)\b", label, re.I)


def search_terms(ontology, query):
    if ontology not in {"uberon", "mondo"} or not 1 <= len(query.strip()) <= 400:
        raise ValueError("Expected a short public Uberon or MONDO query")
    results = []
    exact_error = None
    try:
        url = API + "/search?" + urlencode({"q": query, "ontology": ontology, "exact": "true", "rows": 5})
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=20) as response:
            docs = decode_response(response, None)["response"]["docs"]
        for item in docs:
            key = item.get("obo_id")
            names = [item.get("label", "")] + (item.get("synonym") or [])
            if (term_id(ontology, key) and human_relevant(ontology, names[0])
                    and any(query.strip().casefold() == name.casefold() for name in names)):
                results.append({"id": key, "label": item["label"], "iri": item["iri"], "method": "exact_label_or_synonym"})
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        exact_error = type(exc).__name__

    vector_error = None
    try:
        raw = OlsVectorClient().call("searchClassesWithEmbeddingModel", {
            "query": query, "ontologyId": ontology, "model": EMBEDDING_MODEL, "pageSize": 8, "includeCurations": True})
        for rank, item in enumerate(raw["items"], 1):
            key = item.get("curie")
            if (not term_id(ontology, key) or item.get("isObsolete") or not item.get("label")
                    or not human_relevant(ontology, item["label"][0])):
                continue
            term = next((term for term in results if term["id"] == key), None)
            if term is None:
                term = {"id": key, "label": item["label"][0], "iri": item["iri"], "method": "vector"}
                results.append(term)
            term["vector_rank"] = rank
            term["definition"] = (item.get("definition") or [""])[0][:500]
            term["parents"] = [{"iri": p["iri"], "label": p["label"][0]} for p in item.get("directParent", [])
                               if p.get("iri") and p.get("label")][:5]
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as exc:
        vector_error = type(exc).__name__
    if vector_error and not results:
        raise RuntimeError("OLS vector search failed and no exact term was available: " + vector_error)
    return {"query": query, "backend": "embl_ebi_ols4", "ontology": ontology, "vector_model": EMBEDDING_MODEL,
            "vector_status": "failed" if vector_error else "ok", "vector_error": vector_error,
            "exact_error": exact_error, "source_url": "https://www.ebi.ac.uk/ols4/mcp", "terms": results}


def create_agent(ontology, model):
    from langchain_core.tools import tool
    from langchain_core.runnables import RunnableConfig
    from langgraph.prebuilt import create_react_agent
    from pydantic import BaseModel

    class TermSelection(BaseModel):
        id: str

    @tool
    async def search_ontology_terms(query: str, config: RunnableConfig) -> dict:
        """Look up a public tissue or disease name in OLS. Translate descriptions to concise English."""
        report = await asyncio.to_thread(search_terms, ontology, query)
        config["configurable"]["ols_evidence"].append(report)
        return report

    agent = create_react_agent(model=model, tools=[search_ontology_terms], response_format=TermSelection,
        prompt=f"Find the best {ontology} identifier. Use the OLS tool at least once with a concise English query. "
               "Prefer exact label or synonym evidence. Use vector results and parent evidence when appropriate. "
               "Select only an ID from the tool or return an empty id if the evidence is insufficient.")

    @tool
    async def invoke_ols_ontology(message: str, config: RunnableConfig) -> dict:
        """Resolve a public research description against an ontology."""
        evidence = []
        config = {**config, "configurable": {**config.get("configurable", {}), "ols_evidence": evidence}}
        result = await agent.ainvoke({"messages": [("human", message)]}, config=config)
        selected = getattr(result.get("structured_response"), "id", "")
        verified_ids = {term["id"] for lookup in evidence for term in lookup["terms"]}
        return {"id": selected if selected in verified_ids else None, "ontology_evidence": evidence}

    return invoke_ols_ontology
