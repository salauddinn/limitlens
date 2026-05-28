#!/usr/bin/env python3
"""Tests for limitlens.providers.amp — amp usage parsing."""

import argparse
import unittest
from unittest.mock import patch, MagicMock

from limitlens.providers.amp import get_amp_data


class TestAmpProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(redact=False)
        self.args_redacted = argparse.Namespace(redact=True)

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_usage_parsing_success(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "Signed in as john.doe@example.com\n"
            "amp-free: $1.20/$10.00 remaining (replenishes +$0.50/hour)\n"
            "amp-pro: $5.00/$50.00 remaining\n"
            "Prepaid Credits: $25.00 remaining\n"
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # Test unredacted
        data = get_amp_data(self.args)
        self.assertNotIn("error", data)
        self.assertEqual(data["email"], "john.doe@example.com")
        self.assertEqual(len(data["tiers"]), 3)
        
        t0 = data["tiers"][0]
        self.assertEqual(t0["label"], "amp-free")
        self.assertEqual(t0["remaining"], 1.20)
        self.assertEqual(t0["total"], 10.00)
        self.assertEqual(t0["pct_left"], 12.0)
        self.assertEqual(t0["replenish_rate"], 0.50)

        t1 = data["tiers"][1]
        self.assertEqual(t1["label"], "amp-pro")
        self.assertEqual(t1["remaining"], 5.00)
        self.assertNotIn("replenish_rate", t1)

        t2 = data["tiers"][2]
        self.assertEqual(t2["label"], "Prepaid Credits")
        self.assertEqual(t2["remaining"], 25.00)

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_usage_redaction(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Signed in as john.doe@example.com\namp-free: $1.20/$10.00 remaining\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        data = get_amp_data(self.args_redacted)
        self.assertEqual(data["email"], "jo***@example.com")

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        data = get_amp_data(self.args)
        self.assertEqual(data["error"], "amp not installed")

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_error_exit_code(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Authentication failed"
        mock_run.return_value = mock_result
        
        data = get_amp_data(self.args)
        self.assertEqual(data["error"], "Authentication failed")


if __name__ == "__main__":
    unittest.main()
