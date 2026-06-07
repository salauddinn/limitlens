#!/usr/bin/env python3
"""Tests for limitlens.providers.amp — amp usage parsing."""

import argparse
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from limitlens.providers.amp import get_amp_data, display_amp_text


class TestAmpProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(redact=False, verbose=False, all=False, no_color=False)
        self.args_redacted = argparse.Namespace(redact=True, verbose=False, all=False, no_color=False)
        self.args_verbose = argparse.Namespace(redact=False, verbose=True, all=False, no_color=False)
        self.args_all = argparse.Namespace(redact=False, verbose=False, all=True, no_color=False)
        self.args_no_color = argparse.Namespace(redact=False, verbose=False, all=False, no_color=True)

    @patch("limitlens.providers.amp.load_display_config")
    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_usage_parsing_success(self, mock_run, mock_config):
        mock_config.return_value = {"auto_hide_enabled": False}
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
        self.assertEqual(t0["used"], 8.80)
        self.assertEqual(t0["replenish_rate"], 0.50)
        self.assertTrue(t0["visible"])

        t1 = data["tiers"][1]
        self.assertEqual(t1["label"], "amp-pro")
        self.assertEqual(t1["remaining"], 5.00)
        self.assertNotIn("replenish_rate", t1)

        t2 = data["tiers"][2]
        self.assertEqual(t2["label"], "Prepaid Credits")
        self.assertEqual(t2["remaining"], 25.00)
        self.assertIsNone(t2["used"])

    @patch("limitlens.providers.amp.load_display_config")
    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_usage_redaction(self, mock_run, mock_config):
        mock_config.return_value = {"auto_hide_enabled": False}
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
    def test_amp_subprocess_error(self, mock_run):
        mock_run.side_effect = subprocess.SubprocessError("timeout")
        data = get_amp_data(self.args)
        self.assertEqual(data["error"], "failed to run amp: timeout")

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_error_exit_code(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Authentication failed"
        mock_run.return_value = mock_result
        
        data = get_amp_data(self.args)
        self.assertEqual(data["error"], "Authentication failed")

    @patch("limitlens.providers.amp.subprocess.run")
    def test_amp_error_output_is_cleaned_and_redacted(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Signed in as john.doe@example.com\nError\x1b[=0u\x1b[<u\x1b[?25h"
        mock_run.return_value = mock_result

        data = get_amp_data(self.args_redacted)
        self.assertIn("jo***@example.com", data["error"])
        self.assertNotIn("\x1b", data["error"])

    @patch("limitlens.providers.amp.load_display_config")
    @patch("limitlens.providers.amp.subprocess.run")
    def test_value_error_in_parsing(self, mock_run, mock_config):
        mock_config.return_value = {"auto_hide_enabled": False}
        mock_result = MagicMock()
        mock_result.returncode = 0
        # Line with bad floats
        mock_result.stdout = (
            "amp-free: $ABC/$10.00 remaining\n"
            "Prepaid Credits: $XYZ remaining\n"
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        data = get_amp_data(self.args)
        self.assertEqual(data["tiers"], [])

    @patch("limitlens.providers.amp.load_display_config")
    @patch("limitlens.providers.amp.subprocess.run")
    def test_auto_hide_logic(self, mock_run, mock_config):
        # pct_left < 10, rate <= 0 => visible = False
        # pct_left < 10, rate > 0, hours_to_usable > 24 => visible = False
        # pct_left < 10, rate > 0, hours_to_usable <= 24 => visible = True
        mock_config.return_value = {"auto_hide_enabled": True, "amp_usable_pct": 50.0}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "tier1: $0.00/$100.00 remaining\n" # 0% left, no rate -> hidden
            "tier2: $5.00/$100.00 remaining (replenishes +$0.10/hour)\n" # 5% left, target 50 (45 left), rate 0.1 -> 450 hours -> hidden
            "tier3: $5.00/$100.00 remaining (replenishes +$2.00/hour)\n" # 5% left, target 50 (45 left), rate 2.0 -> 22.5 hours -> visible
            "tier4: $90.00/$100.00 remaining\n" # 90% left -> visible
            "tier5: $90.00 remaining\n" # pct_left None -> visible
        )
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        data = get_amp_data(self.args)
        tiers = data["tiers"]
        self.assertEqual(len(tiers), 5)
        self.assertFalse(tiers[0]["visible"])
        self.assertFalse(tiers[1]["visible"])
        self.assertTrue(tiers[2]["visible"])
        self.assertTrue(tiers[3]["visible"])
        self.assertTrue(tiers[4]["visible"])

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.print_error")
    def test_display_amp_text_error(self, mock_print_error, mock_section):
        data = {"error": "Some error"}
        display_amp_text(data, self.args)
        mock_section.assert_called_once_with("Amp", self.args)
        mock_print_error.assert_called_once_with("Some error", self.args)

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.identity_line")
    @patch("builtins.print")
    def test_display_amp_text_no_visible_tiers(self, mock_print, mock_identity, mock_section):
        data = {
            "email": "user@example.com",
            "tiers": [{
                "visible": False,
                "label": "T1",
                "remaining": 1.0,
                "total": 10.0,
                "pct_left": 10.0,
                "pct_used": 90.0
            }],
            "raw_output": "hidden output"
        }
        # Not verbose, not all -> function returns early if all hidden
        display_amp_text(data, self.args)
        mock_section.assert_not_called()
        
        # Test with 'all' flag
        display_amp_text(data, self.args_all)
        mock_section.assert_called_with("Amp", self.args_all)
        mock_identity.assert_called_with("amp", "user@example.com", self.args_all)
        # Should still print the tier because 'all' overrides visible=False
        # Wait, if all overrides, then visible_tiers has the tier. Let's see the code:
        # if not tier.get("visible", True) and not (getattr(args, "verbose", False) or getattr(args, "all", False)): continue
        # So tier is added to visible_tiers.

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.identity_line")
    @patch("limitlens.providers.amp.print_c")
    def test_display_amp_text_empty_visible_tiers_with_no_tiers_list(self, mock_print_c, mock_identity, mock_section):
        data = {
            "email": "user@example.com",
            "tiers": [],
            "raw_output": "raw tier info"
        }
        # verbose mode -> empty visible tiers will trigger printing raw output
        display_amp_text(data, self.args_verbose)
        mock_section.assert_called_with("Amp", self.args_verbose)
        mock_print_c.assert_called_with("    raw tier info", "\033[90m", False)

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.identity_line")
    @patch("builtins.print")
    def test_display_amp_text_standard(self, mock_print, mock_identity, mock_section):
        data = {
            "email": None,
            "tiers": [
                {
                    "label": "My Test Tier",
                    "remaining": 2.50,
                    "total": 10.0,
                    "pct_left": 25.0,
                    "pct_used": 75.0,
                    "visible": True,
                    "replenish": "+$1.0/hour",
                    "replenish_rate": 1.0,
                },
                {
                    "label": "Credit Tier",
                    "remaining": 50.0,
                    "total": None,
                    "pct_left": None,
                    "pct_used": None,
                    "visible": True,
                },
                {
                    "label": "Very long tier name that will be truncated",
                    "remaining": 5.0,
                    "total": 5.0,
                    "pct_left": 100.0,
                    "pct_used": 0.0,
                    "visible": True,
                }
            ]
        }
        display_amp_text(data, self.args)
        mock_section.assert_called_with("Amp", self.args)
        mock_identity.assert_called_with("amp", "unknown", self.args)
        
        # We can check what was printed by inspecting mock_print.call_args_list
        calls = mock_print.call_args_list
        self.assertTrue(len(calls) >= 3)
        self.assertIn("my-test-tier", calls[0][0][0])
        self.assertIn("credit-tier", calls[1][0][0])
        self.assertIn("very-long-ti", calls[2][0][0])

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.identity_line")
    @patch("builtins.print")
    def test_display_amp_text_no_color(self, mock_print, mock_identity, mock_section):
        data = {
            "email": "user@example.com",
            "tiers": [
                {
                    "label": "T1",
                    "remaining": 1.0,
                    "total": 10.0,
                    "pct_left": 10.0,
                    "pct_used": 90.0,
                    "visible": True,
                },
                {
                    "label": "T2",
                    "remaining": 10.0,
                    "total": None,
                    "pct_left": None,
                    "pct_used": None,
                    "visible": True,
                }
            ]
        }
        display_amp_text(data, self.args_no_color)
        calls = mock_print.call_args_list
        self.assertNotIn("\033[90m", calls[0][0][0])
        self.assertNotIn("\033[0m", calls[0][0][0])
        self.assertNotIn("\033[90m", calls[1][0][0])

    @patch("limitlens.providers.amp.section")
    @patch("limitlens.providers.amp.identity_line")
    @patch("builtins.print")
    def test_display_amp_text_verbose_full_at(self, mock_print, mock_identity, mock_section):
        data = {
            "email": "user@example.com",
            "tiers": [
                {
                    "label": "T1",
                    "remaining": 1.0,
                    "total": 10.0,
                    "pct_left": 10.0,
                    "pct_used": 90.0,
                    "visible": True,
                    "replenish": "+$1.0/hour",
                    "replenish_rate": 1.0,
                }
            ]
        }
        display_amp_text(data, self.args_verbose)
        # Should contain full_at since verbose is True and rate > 0
        calls = mock_print.call_args_list
        output = calls[0][0][0]
        self.assertIn("replenishes +$1.0/hour", output)
        self.assertIn("full at", output)

if __name__ == "__main__":
    unittest.main()
