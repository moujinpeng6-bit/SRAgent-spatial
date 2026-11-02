import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sragent_spatial.dependencies import dependency_report, toolkit_bin


class DependencyTests(unittest.TestCase):
    def test_project_name_alone_is_not_bigquery_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "offline-project", "CLOUDSDK_CONFIG": folder,
        }, clear=True):
            result = dependency_report()["bigquery"]
            self.assertTrue(result["project_configured"])
            self.assertFalse(result["requirements_detected"])

    def test_both_sra_executables_are_required(self):
        suffix = ".exe" if os.name == "nt" else ""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ("sra-stat" + suffix)).touch()
            with patch.dict(os.environ, {"SRA_TOOLKIT_BIN": folder}), \
                    patch("sragent_spatial.dependencies.ROOT", root):
                self.assertIsNone(toolkit_bin())
                (root / ("fastq-dump" + suffix)).touch()
                self.assertEqual(toolkit_bin(), root)

    def test_presence_check_does_not_claim_live_authentication(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "GOOGLE_CLOUD_PROJECT": "offline-project", "CLOUDSDK_CONFIG": folder,
        }, clear=True):
            (Path(folder) / "application_default_credentials.json").write_text("not real credentials")
            result = dependency_report()["bigquery"]
            self.assertTrue(result["requirements_detected"])
            self.assertFalse(result["live_verified"])
