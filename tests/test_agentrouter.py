#!/usr/bin/env python3
"""Tests for limitlens.providers.agentrouter — AgentRouter/Kilo quota parsing."""

import argparse
import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from limitlens.providers.agentrouter import (
    display_agentrouter_text,
    get_agentrouter_data,
    parse_agentrouter_quota,
)


class TestAgentRouterProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(redact=True, no_color=True, verbose=False, all=False)

    @patch("limitlens.providers.agentrouter.load_display_config", return_value={"auto_hide_enabled": False})
    def test_parse_agentrouter_quota_response(self, mock_display):
        payload = {
            "data": {
                "username": "github_145176",
                "display_name": "Kilo Code",
                "quota": 84917038,
                "used_quota": 2582962,
                "request_count": 42,
                "group": "default",
            },
            "success": True,
        }

        data = parse_agentrouter_quota(payload, self.args)

        tier = data["tiers"][0]
        self.assertEqual(tier["total"], 84917038)
        self.assertEqual(tier["used"], 2582962)
        self.assertEqual(tier["remaining"], 82334076)
        self.assertAlmostEqual(tier["pct_left"], 96.9584, places=3)
        self.assertEqual(data["request_count"], 42)

    @patch("limitlens.providers.agentrouter.load_display_config", return_value={"auto_hide_enabled": False})
    def test_parse_clamps_overuse(self, mock_display):
        data = parse_agentrouter_quota({"data": {"quota": 100, "used_quota": 120}}, self.args)

        tier = data["tiers"][0]
        self.assertEqual(tier["remaining"], 0.0)
        self.assertEqual(tier["pct_used"], 120.0)

    @patch("limitlens.providers.agentrouter.load_display_config", return_value={"auto_hide_enabled": False})
    def test_display_agentrouter_quota(self, mock_display):
        data = parse_agentrouter_quota({
            "data": {
                "display_name": "Kilo Code",
                "quota": 84917038,
                "used_quota": 2582962,
                "request_count": 42,
            }
        }, self.args)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_agentrouter_text(data, self.args)

        output = buf.getvalue()
        self.assertIn("AgentRouter / Kilo Code", output)
        self.assertIn("97.0% left", output)
        self.assertIn("82.33M/84.92M units", output)
        self.assertIn("42", output)

    @patch("limitlens.providers.agentrouter.load_limitlens_config", return_value={"agentrouter": {}})
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_auth_without_manual(self, mock_config):
        data = get_agentrouter_data(self.args)

        self.assertIn("AGENTROUTER_API_TOKEN or AGENTROUTER_COOKIE not set", data["error"])

    @patch("limitlens.providers.agentrouter.load_display_config", return_value={"auto_hide_enabled": False})
    @patch("limitlens.providers.agentrouter.load_limitlens_config", return_value={
        "agentrouter": {"manual": {"quota": 100, "used_quota": 25, "request_count": 2}}
    })
    @patch.dict(os.environ, {}, clear=True)
    def test_manual_fallback_without_auth(self, mock_config, mock_display):
        data = get_agentrouter_data(self.args)

        self.assertEqual(data["tiers"][0]["remaining"], 75.0)

    @patch("limitlens.providers.agentrouter.urllib.request.urlopen")
    @patch("limitlens.providers.agentrouter.load_limitlens_config", return_value={"agentrouter": {}})
    @patch.dict(os.environ, {"AGENTROUTER_COOKIE": "test-cookie", "AGENTROUTER_NEW_API_USER": "145176"})
    def test_live_request_uses_env_auth(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": {"quota": 100, "used_quota": 1}, "success": True}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        data = get_agentrouter_data(self.args)

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.headers["Cookie"], "test-cookie")
        self.assertEqual(req.headers["New-api-user"], "145176")
        self.assertEqual(data["tiers"][0]["remaining"], 99.0)

    @patch("limitlens.providers.agentrouter.urllib.request.urlopen")
    @patch("limitlens.providers.agentrouter.load_limitlens_config", return_value={"agentrouter": {}})
    @patch.dict(os.environ, {"AGENTROUTER_COOKIE": "test-cookie"})
    def test_network_error(self, mock_config, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        data = get_agentrouter_data(self.args)

        self.assertIn("Connection refused", data["error"])


if __name__ == "__main__":
    unittest.main()
