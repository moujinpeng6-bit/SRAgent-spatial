import asyncio
import io
import json
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sragent_spatial import deepseek_model, upstream_registry
from sragent_spatial.agents import credentials_present, doctor, predict_agent, provider
from sragent_spatial.cli import main as cli_main
from test_screening import packet


class DeepSeekModelTests(unittest.TestCase):
    def test_model_ids_are_provider_checked(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deepseek_model.validate_model("deepseek-chat"), "deepseek-chat")
            self.assertEqual(deepseek_model.validate_model("deepseek-reasoner"), "deepseek-reasoner")
            self.assertEqual(deepseek_model.validate_model(None), deepseek_model.DEFAULT_MODEL)
            for bad in ("gpt-4o", "claude-3-7-sonnet", "deepseek", "deepseek chat"):
                with self.assertRaises(ValueError):
                    deepseek_model.validate_model(bad)

    def test_missing_key_is_explicit_and_does_not_echo_secrets(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                deepseek_model.build("deepseek-chat")
        self.assertIn("DEEPSEEK_API_KEY", str(caught.exception))

    def test_client_targets_deepseek_base_url(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "offline-test-key"}, clear=True):
            client = deepseek_model.build("deepseek-chat", temperature=0.1, max_tokens=64)
        self.assertEqual(client.model_name, "deepseek-chat")
        self.assertEqual(str(client.openai_api_base).rstrip("/"), deepseek_model.DEFAULT_BASE_URL)
        self.assertEqual(client.max_tokens, 64)

    def test_install_patches_upstream_factory_before_agent_imports(self):
        fake_utils = types.ModuleType("SRAgent.agents.utils")
        fake_utils.set_model = object()
        modules = {"SRAgent": types.ModuleType("SRAgent"),
                   "SRAgent.agents": types.ModuleType("SRAgent.agents"),
                   "SRAgent.agents.utils": fake_utils}
        with patch.dict(sys.modules, modules):
            deepseek_model.install()
            self.assertIs(fake_utils.set_model, deepseek_model.set_model)
            self.assertTrue(deepseek_model.is_installed())

    def test_install_refuses_after_upstream_agents_were_imported(self):
        modules = {name: types.ModuleType(name) for name in
                   ("SRAgent", "SRAgent.agents", "SRAgent.agents.utils", "SRAgent.agents.esearch")}
        with patch.dict(sys.modules, modules):
            with self.assertRaises(RuntimeError) as caught:
                deepseek_model.install()
        self.assertIn("SRAgent.agents.esearch", str(caught.exception))


class RegistryTests(unittest.TestCase):
    def test_describe_is_metadata_only(self):
        rows = upstream_registry.describe()
        keys = [row["key"] for row in rows]
        self.assertIn("esearch", keys)
        self.assertIn("sragent", keys)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("SRAgent.agents.esearch", sys.modules)
        summary = upstream_registry.summary()
        self.assertEqual(summary["total"], len(rows))
        self.assertEqual(summary["blocked"], 2)

    def test_every_module_declares_a_tool_and_factory(self):
        for row in upstream_registry.describe():
            self.assertTrue(row["factory"].startswith("create_"))
            self.assertTrue(row["tool"].startswith("invoke_"))
            self.assertIn(row["spatial_status"], {"direct", "rewrite", "blocked"})

    def test_unknown_module_is_rejected(self):
        with self.assertRaises(ValueError):
            upstream_registry.load("nope")

    def test_payload_follows_tool_schema(self):
        message_tool = types.SimpleNamespace(args={"message": "str"})
        task_tool = types.SimpleNamespace(args={"task": "str"})
        self.assertEqual(upstream_registry.payload_for(message_tool, "hi"), {"message": "hi"})
        self.assertEqual(upstream_registry.payload_for(task_tool, "hi"), {"task": "hi"})
        with self.assertRaises(RuntimeError):
            upstream_registry.payload_for(types.SimpleNamespace(args={"other": "str"}), "hi")

    def test_as_text_handles_agent_shapes(self):
        self.assertEqual(upstream_registry.as_text("plain"), "plain")
        self.assertEqual(upstream_registry.as_text({"messages": [types.SimpleNamespace(content="x")]}), "x")
        self.assertEqual(upstream_registry.as_text([{"text": "a"}, {"text": "b"}]), "a\nb")


class ProviderTests(unittest.TestCase):
    def test_provider_selection_order(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider(), "openai")
            self.assertFalse(credentials_present())
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}, clear=True):
            self.assertEqual(provider(), "deepseek")
            self.assertTrue(credentials_present())
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k", "SPATIAL_PROVIDER": "openai"}, clear=True):
            self.assertEqual(provider(), "openai")
            self.assertFalse(credentials_present())

    def test_doctor_reports_without_importing_upstream_agents(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-verysecret", "SPATIAL_MODEL": "deepseek-chat"}, clear=True):
            report = doctor()
        self.assertEqual(report["provider"], "deepseek")
        self.assertTrue(report["deepseek_api_key_configured"])
        self.assertIn("search_modules", report)
        self.assertNotIn("sk-verysecret", json.dumps(report))


class SragentMethodTests(unittest.TestCase):
    def test_sragent_method_puts_supervisor_in_tool_list(self):
        sample = packet()
        seen = {}

        class Graph:
            def __init__(self, tools):
                self.tools = tools

            async def ainvoke(self, payload, config):
                seen["tools"] = self.tools
                return {"messages": [types.SimpleNamespace(content=json.dumps(
                    {"accession": sample["accession"], "label": "unknown",
                     "reason": "insufficient evidence", "evidence": []}))]}

        factories = (lambda model_name: object(), lambda **kwargs: object(),
                     lambda model, tools, prompt: Graph(tools), lambda fn: fn)
        supervisor = object()
        with patch("sragent_spatial.agents.configure", return_value=factories), \
                patch("sragent_spatial.agents.upstream_registry.load", return_value=supervisor) as loader:
            result = asyncio.run(predict_agent(sample, "sragent", "deepseek-chat"))
        self.assertEqual(result["method"], "sragent")
        self.assertIs(seen["tools"][0], supervisor)
        self.assertEqual(len(seen["tools"]), 3)
        loader.assert_called_once_with("sragent", "deepseek-chat", provider=provider())
        self.assertEqual(result["original_scope"],
                         "complete_upstream_sragent_supervisor_plus_sample_bound_spatial_tools")


class CliTests(unittest.TestCase):
    def test_modules_command_lists_every_module(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli_main(["modules"])
        self.assertEqual(code, 0)
        printed = buffer.getvalue()
        self.assertIn("esearch", printed)
        self.assertIn("tissue_ontology", printed)

    def test_modules_command_can_write_json(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "modules.json"
            with redirect_stdout(io.StringIO()):
                code = cli_main(["modules", "--out", str(out)])
            self.assertEqual(code, 0)
            rows = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), len(upstream_registry.MODULES))


if __name__ == "__main__":
    unittest.main()
