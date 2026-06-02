#!/usr/bin/env python3
"""Tests for limitlens.providers.commandcode."""

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from limitlens.providers.commandcode import (
    display_commandcode_text,
    get_commandcode_data,
    parse_commandcode_credits,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestCommandCodeProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(no_color=True, verbose=False, all=False)

    def test_parse_credits_response(self):
        data = parse_commandcode_credits({
            "credits": {
                "belowThreshold": False,
                "creditThreshold": 0,
                "monthlyCredits": 0,
                "purchasedCredits": 4.3507,
                "premiumMonthlyCredits": 0,
                "opensourceMonthlyCredits": 0,
            }
        }, self.args)

        self.assertAlmostEqual(data["available"], 4.3507)
        self.assertAlmostEqual(data["credits"]["purchased"], 4.3507)
        self.assertEqual(data["tiers"][0]["pct_left"], 100.0)

    def test_display_credits(self):
        data = parse_commandcode_credits({"credits": {"purchasedCredits": 4.3507}}, self.args)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_commandcode_text(data, self.args)

        output = buf.getvalue()
        self.assertIn("Command Code", output)
        self.assertIn("4.3507/4.3507 credits", output)
        self.assertIn("purchased", output)
        self.assertIn("4.3507 credits", output)

    def test_display_nonzero_credit_types(self):
        data = parse_commandcode_credits({
            "credits": {
                "monthlyCredits": 1.25,
                "purchasedCredits": 4.3507,
                "premiumMonthlyCredits": 2.5,
                "opensourceMonthlyCredits": 3.75,
            }
        }, self.args)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_commandcode_text(data, self.args)

        output = buf.getvalue()
        self.assertIn("purchased", output)
        self.assertIn("monthly", output)
        self.assertIn("premium monthly", output)
        self.assertIn("opensource monthly", output)
        self.assertIn("11.8507/11.8507 credits", output)

    @patch.dict("os.environ", {"COMMANDCODE_COOKIE": "session=redacted"}, clear=True)
    @patch("limitlens.providers.commandcode.load_limitlens_config", return_value={"commandcode": {}})
    @patch("urllib.request.urlopen", return_value=FakeResponse({"credits": {"purchasedCredits": 2.5}}))
    def test_get_credits_uses_env_cookie(self, mock_urlopen, mock_config):
        data = get_commandcode_data(self.args)

        self.assertAlmostEqual(data["available"], 2.5)
        request = mock_urlopen.call_args[0][0]
        self.assertIn("Cookie", request.headers)
        self.assertEqual(request.headers["Cookie"], "session=redacted")

    @patch.dict("os.environ", {}, clear=True)
    @patch("limitlens.providers.commandcode.load_limitlens_config", return_value={"commandcode": {}})
    def test_missing_auth_returns_error(self, mock_config):
        data = get_commandcode_data(self.args)

        self.assertIn("error", data)
        self.assertIn("COMMANDCODE_COOKIE", data["error"])


if __name__ == "__main__":
    unittest.main()
