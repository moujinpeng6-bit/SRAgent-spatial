import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class HiddenKeyLauncherTests(unittest.TestCase):
    def launcher(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "run_agents.py"
        spec = importlib.util.spec_from_file_location("hidden_key_launcher", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fresh_key_used_and_previous_environment_restored_on_failure(self):
        launcher = self.launcher()

        def fail_run(args):
            self.assertEqual(os.environ["OPENAI_API_KEY"], "fresh-offline-test")
            raise RuntimeError("offline failure")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "old-offline-test"}, clear=True), \
                patch("sys.argv", ["run_agents.py", "--model", "gpt-test", "--prompt-key"]), \
                patch("sys.stdin.isatty", return_value=True), \
                patch("getpass.getpass", return_value="fresh-offline-test"), \
                patch("sragent_spatial.cli.main", side_effect=fail_run), patch("os.chdir"):
            with self.assertRaises(RuntimeError):
                launcher.main()
            self.assertEqual(os.environ["OPENAI_API_KEY"], "old-offline-test")

    def test_temporary_key_removed_after_success(self):
        launcher = self.launcher()
        with patch.dict(os.environ, {}, clear=True), \
                patch("sys.argv", ["run_agents.py", "--model", "gpt-test"]), \
                patch("sys.stdin.isatty", return_value=True), \
                patch("getpass.getpass", return_value="offline-test"), \
                patch("sragent_spatial.cli.main", return_value=0), patch("os.chdir"):
            self.assertEqual(launcher.main(), 0)
            self.assertNotIn("OPENAI_API_KEY", os.environ)
