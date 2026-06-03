#!/usr/bin/env python3
"""Tests for limitlens.providers.pioneer — pioneer usage parsing."""

import argparse
import json
import os
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

from limitlens.providers.pioneer import get_pioneer_data


class TestPioneerProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(redact=False)
        self.args_redacted = argparse.Namespace(redact=True)

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_billing_parsing_success(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": {
                "email": "john.doe@example.com",
                "plan": "Pro Plan",
                "tiers": [
                    {"label": "Credits", "remaining": 10.5, "total": 50.0, "used": 39.5},
                    {"label": "Bonus", "remaining": 5.0, "total": 5.0, "used": 0.0}
                ]
            }
        }).encode("utf-8")
        # Ensure context manager works
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # Test unredacted
        data = get_pioneer_data(self.args)
        self.assertNotIn("error", data)
        self.assertEqual(data["email"], "john.doe@example.com")
        self.assertEqual(len(data["tiers"]), 2)

        t0 = data["tiers"][0]
        self.assertEqual(t0["label"], "Credits")
        self.assertEqual(t0["remaining"], 10.5)
        self.assertEqual(t0["total"], 50.0)
        self.assertEqual(t0["pct_left"], 21.0)

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_email_redaction(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "email": "john.doe@example.com",
            "credits_remaining": 10.0,
            "credits_total": 50.0
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        data = get_pioneer_data(self.args_redacted)
        self.assertEqual(data["email"], "jo***@example.com")

    @patch.dict(os.environ, {}, clear=True)
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    def test_pioneer_token_not_set(self, mock_config):
        data = get_pioneer_data(self.args)
        self.assertEqual(data["error"], "PIONEER_API_TOKEN environment variable not set")

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_network_error(self, mock_config, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        data = get_pioneer_data(self.args)
        self.assertIn("Connection refused", data["error"])

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_invalid_json(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Not JSON"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        data = get_pioneer_data(self.args)
        self.assertEqual(data["error"], "Invalid JSON response from Pioneer API")

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_empty_response(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"{}"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        data = get_pioneer_data(self.args)
        self.assertEqual(data["error"], "Empty response from Pioneer API")

    @patch("limitlens.providers.pioneer.urllib.request.urlopen")
    @patch("limitlens.providers.pioneer.load_limitlens_config", return_value={"pioneer": {"team_id": "team-1"}})
    @patch.dict(os.environ, {"PIONEER_API_TOKEN": "test-token"})
    def test_pioneer_full_status_parsing(self, mock_config, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "team_id": "team-1",
            "team_name": "No's Team",
            "payment_plan": "hobby",
            "total_usage": 605.7127,
            "credit_limit": 3000.0,
            "free_tier_remaining": 2394.2873,
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        data = get_pioneer_data(self.args)

        req = mock_urlopen.call_args[0][0]
        self.assertIn("/billing/team/team-1/full-status", req.full_url)
        self.assertEqual(data["team_name"], "No's Team")
        self.assertEqual(data["plan"], "hobby")
        self.assertEqual(len(data["tiers"]), 1)
        self.assertAlmostEqual(data["tiers"][0]["total"], 30.0)
        self.assertAlmostEqual(data["tiers"][0]["remaining"], 23.942873)
        self.assertAlmostEqual(data["tiers"][0]["used"], 6.057127)


if __name__ == "__main__":
    unittest.main()
