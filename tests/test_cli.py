#!/usr/bin/env python3
"""Tests for limitlens.cli."""

import argparse
import sys
import unittest
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
        with patch.object(sys, "argv", test_args):
            main()

        mock_refresh_all.assert_called_once_with()
        mock_print.assert_not_called()

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    @patch("limitlens.recommendations.display_one_line")
    def test_one_line_quick_recommendation(
        self, mock_display_one_line, mock_record, mock_opencode, mock_ag, mock_amp, mock_codex
    ):
        mock_codex.return_value = {}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}

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
    def test_waste_report(self, mock_codex, mock_display_waste, mock_compute_waste):
        mock_codex.return_value = {}
        mock_compute_waste.return_value = []
        
        test_args = ["limitlens", "--waste", "--days", "14"]
        with patch.object(sys, "argv", test_args):
            main()

        mock_compute_waste.assert_called_once_with(days=14)
        mock_display_waste.assert_called_once()

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
