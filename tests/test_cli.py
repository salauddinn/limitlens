#!/usr/bin/env python3
"""Tests for limitlens.cli."""

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

from limitlens.cli import main


class TestCLI(unittest.TestCase):
    @patch("limitlens.providers.codex.refresh_all_accounts")
    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_json_refresh_all_does_not_print_status_line(
        self, mock_record, mock_opencode, mock_ag, mock_amp, mock_codex, mock_print, mock_refresh_all
    ):
        mock_codex.return_value = {"accounts": []}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}

        test_args = ["limitlens", "--json", "--tool", "codex", "--refresh-all", "--no-record"]
        with patch.object(sys, "argv", test_args), redirect_stdout(io.StringIO()):
            main()

        mock_refresh_all.assert_called_once()
        mock_print.assert_not_called()

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_agentrouter_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    @patch("limitlens.recommendations.display_one_line")
    def test_one_line_quick_recommendation(
        self, mock_display_one_line, mock_record, mock_custom, mock_agentrouter, mock_opencode, mock_ag, mock_amp, mock_codex
    ):
        mock_codex.return_value = {}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}
        mock_agentrouter.return_value = {}
        mock_custom.return_value = {}

        test_args = ["limitlens", "--quick"]
        with patch.object(sys, "argv", test_args):
            # prevent SystemExit on argparse error by catching it, but shouldn't happen here
            main()

        # Should fetch all providers for recommendations
        mock_codex.assert_called_once()
        mock_amp.assert_called_once()
        mock_ag.assert_called_once()
        
        # Should record snapshot
        mock_record.assert_called_once()

        # Should call display_one_line with "quick"
        mock_display_one_line.assert_called_once()
        self.assertEqual(mock_display_one_line.call_args[0][0], "quick")

    @patch("limitlens.waste_tracker.compute_waste")
    @patch("limitlens.waste_tracker.display_waste_report")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_agentrouter_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_waste_report(
        self, mock_record, mock_custom, mock_agentrouter, mock_opencode, mock_ag, mock_amp, mock_codex, mock_display_waste, mock_compute_waste
    ):
        mock_codex.return_value = {}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}
        mock_agentrouter.return_value = {}
        mock_custom.return_value = {}
        mock_compute_waste.return_value = []
        
        test_args = ["limitlens", "--waste", "--days", "14"]
        with patch.object(sys, "argv", test_args):
            main()

        mock_compute_waste.assert_called_once_with(days=14)
        mock_display_waste.assert_called_once()

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_agentrouter_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_provider_failure_isolated_in_json(
        self, mock_record, mock_custom, mock_agentrouter, mock_opencode, mock_ag, mock_amp, mock_codex
    ):
        mock_codex.return_value = {"accounts": []}
        mock_amp.side_effect = TypeError("bad local output")
        mock_ag.return_value = {"profiles": []}
        mock_opencode.return_value = {"opencode": {"windows": []}, "copilot_cli": {"disabled": True}}
        mock_agentrouter.return_value = {"tiers": []}
        mock_custom.return_value = {"tools": []}

        test_args = ["limitlens", "--json", "--tool", "all", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), redirect_stdout(buf):
            main()

        payload = json.loads(buf.getvalue())
        self.assertIn("amp provider failed: TypeError: bad local output", payload["amp"]["error"])
        self.assertIn("codex", payload)
        self.assertIn("antigravity", payload)

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": True},
        "pioneer": {"enabled": False},
        "agentrouter": {"enabled": False},
        "custom_tools": {"enabled": False},
    })
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_pioneer_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_all_skips_disabled_pioneer(
        self, mock_record, mock_pioneer, mock_opencode, mock_ag, mock_amp, mock_codex, mock_config
    ):
        mock_codex.return_value = {"accounts": []}
        mock_amp.return_value = {}
        mock_ag.return_value = {"profiles": []}
        mock_opencode.return_value = {"opencode": {"windows": []}, "copilot_cli": {"disabled": True}}

        test_args = ["limitlens", "--json", "--tool", "all", "--no-record"]
        with patch.object(sys, "argv", test_args), redirect_stdout(io.StringIO()):
            main()

        mock_pioneer.assert_not_called()

    @patch("limitlens.cli.load_limitlens_config", return_value={"pioneer": {"enabled": False}})
    @patch("limitlens.cli.get_pioneer_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_direct_pioneer_runs_when_disabled_in_all(self, mock_record, mock_pioneer, mock_config):
        mock_pioneer.return_value = {"tiers": []}

        test_args = ["limitlens", "--json", "--tool", "pioneer", "--no-record"]
        with patch.object(sys, "argv", test_args), redirect_stdout(io.StringIO()):
            main()

        mock_pioneer.assert_called_once()

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": False},
        "pioneer": {"enabled": False},
        "agentrouter": {"enabled": False},
        "custom_tools": {"enabled": True},
    })
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_all_fetches_enabled_custom_tools(
        self, mock_record, mock_custom, mock_opencode, mock_ag, mock_amp, mock_config
    ):
        mock_amp.return_value = {}
        mock_ag.return_value = {"profiles": []}
        mock_opencode.return_value = {"opencode": {"windows": []}, "copilot_cli": {"disabled": True}}
        mock_custom.return_value = {"tools": []}

        test_args = ["limitlens", "--json", "--tool", "all", "--no-record"]
        with patch.object(sys, "argv", test_args), redirect_stdout(io.StringIO()):
            main()

        mock_custom.assert_called_once()

    @patch("limitlens.waste_tracker.reset_snapshots")
    @patch("limitlens.cli.print_c")
    def test_reset_waste(self, mock_print, mock_reset):
        mock_reset.return_value = True
        
        test_args = ["limitlens", "--reset-waste"]
        with patch.object(sys, "argv", test_args):
            main()

        mock_reset.assert_called_once()
        # Verify success message was printed
        self.assertTrue(any("waste history cleared" in str(call) for call in mock_print.mock_calls))


if __name__ == "__main__":
    unittest.main()
