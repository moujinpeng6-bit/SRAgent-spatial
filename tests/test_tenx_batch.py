import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sragent_spatial.tenx_batch import run_batch
from sragent_spatial.tenx_cache import CacheInputError


class TenxBatchTests(unittest.TestCase):
    def test_resume_continues_after_missing_input_and_skips_success(self):
        passed = {
            "events": [], "preparation": {"status": "prepared"},
            "samples": [{"status": "correspondence_passed", "cells": 4}],
            "agent_tool_loop_verified": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cache"
            root.mkdir()
            out = root.parent / "out"
            ids = ["missing", "valid"]
            with patch("sragent_spatial.tenx_batch.TenxCacheSession",
                       side_effect=[CacheInputError("missing_image", "missing"), object(),
                                    CacheInputError("missing_image", "missing")]), patch(
                "sragent_spatial.tenx_batch.run_validation_agent", new_callable=AsyncMock
            ) as agent:
                agent.return_value = passed
                first = asyncio.run(run_batch(root, ids, out, "test", timeout=30))
                second = asyncio.run(run_batch(root, ids, out, "test", timeout=30, resume=True))
            self.assertEqual(first["samples"]["missing"]["status"], "missing_image")
            self.assertEqual(first["samples"]["valid"]["status"], "correspondence_passed")
            self.assertEqual(second["samples"]["missing"]["attempts"], 2)
            self.assertEqual(second["samples"]["valid"]["attempts"], 1)
            self.assertEqual(agent.await_count, 1)
