#!/usr/bin/env python3
"""Tests for limitlens.cli."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from limitlens.cli import main


TEST_CONFIG = {
    "codex": {"enabled": True, "auto_refresh": True},
    "opencode": {"enabled": True},
    "pi": {"enabled": True},
    "pioneer": {"enabled": False},
    "commandcode": {"enabled": False},
    "custom_tools": {"enabled": False},
}


class TestCLI(unittest.TestCase):
    @patch("limitlens.providers.codex.refresh_all_accounts")
    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_json_sync_codex_does_not_print_status_line(
        self, mock_record, mock_opencode, mock_ag, mock_amp, mock_codex, mock_print, mock_refresh_all
    ):
        mock_codex.return_value = {"accounts": []}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}
        mock_refresh_all.return_value = {"default": {"ok": True, "error": None}}

        test_args = ["limitlens", "--json", "--tool", "codex", "--sync-codex", "--no-record"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()) as buf:
            main()

        mock_refresh_all.assert_called_once()
        mock_print.assert_not_called()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["_refresh"]["codex"]["mode"], "sync_all")
        self.assertEqual(payload["_refresh"]["codex"]["results"], {"default": {"ok": True, "error": None}})

    @patch("limitlens.providers.codex.refresh_accounts")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_stale_codex_refreshes_by_default(self, mock_record, mock_codex, mock_refresh_accounts):
        mock_refresh_accounts.return_value = {"default": {"ok": True, "error": None}}
        stale = {
            "accounts": [
                {
                    "name": "default",
                    "limits": [
                        {
                            "label": "5h window",
                            "left_percent": 100.0,
                            "reset_time": None,
                            "reset_time_fmt": "likely reset (stale data)",
                            "is_stale": True,
                        }
                    ],
                }
            ]
        }
        fresh = {
            "accounts": [
                {
                    "name": "default",
                    "limits": [
                        {
                            "label": "5h window",
                            "left_percent": 80.0,
                            "reset_time": None,
                            "reset_time_fmt": "—",
                            "is_stale": False,
                        }
                    ],
                }
            ]
        }
        mock_codex.side_effect = [stale, fresh]

        test_args = ["limitlens", "--json", "--tool", "codex", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()

        mock_refresh_accounts.assert_called_once()
        self.assertEqual(mock_refresh_accounts.call_args[0][0], ["default"])
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["codex"]["accounts"][0]["limits"][0]["is_stale"])
        self.assertEqual(payload["_refresh"]["codex"]["mode"], "stale_accounts")
        self.assertEqual(payload["_refresh"]["codex"]["accounts"], ["default"])

    @patch("limitlens.providers.codex.refresh_accounts", return_value={"default": {"ok": False, "error": "timeout"}})
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_stale_codex_refresh_result_is_reported_when_still_stale(self, mock_record, mock_codex, mock_refresh_accounts):
        stale = {
            "accounts": [
                {
                    "name": "default",
                    "limits": [{"label": "5h window", "left_percent": 100.0, "is_stale": True}],
                }
            ]
        }
        mock_codex.side_effect = [stale, stale]

        test_args = ["limitlens", "--json", "--tool", "codex", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()

        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["codex"]["accounts"][0]["limits"][0]["is_stale"])
        self.assertEqual(payload["_refresh"]["codex"]["results"]["default"]["error"], "timeout")

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    @patch("limitlens.recommendations.display_one_line")
    def test_one_line_quick_recommendation(
        self, mock_display_one_line, mock_record, mock_custom, mock_opencode, mock_ag, mock_amp, mock_codex
    ):
        mock_codex.return_value = {}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}
        mock_custom.return_value = {}

        test_args = ["limitlens", "--quick"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG):
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
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_waste_report(
        self, mock_record, mock_custom, mock_opencode, mock_ag, mock_amp, mock_codex, mock_display_waste, mock_compute_waste
    ):
        mock_codex.return_value = {}
        mock_amp.return_value = {}
        mock_ag.return_value = {}
        mock_opencode.return_value = {}
        mock_custom.return_value = {}
        mock_compute_waste.return_value = []

        test_args = ["limitlens", "--waste", "--days", "14"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG):
            main()

        mock_compute_waste.assert_called_once_with(days=14, config=TEST_CONFIG)
        mock_display_waste.assert_called_once()

    def test_usage_json_shape_and_observed_fetch_once(self):
        config = dict(TEST_CONFIG)
        config["pi"] = {"enabled": False}
        config["cursor"] = {"enabled": False}
        now = datetime.now(timezone.utc)
        observed = {
            "opencode": {
                "windows": [
                    {
                        "days": 7,
                        "models": [
                            {
                                "provider": "anthropic",
                                "model": "claude",
                                "requests": 1,
                                "cost": 0.5,
                                "tokens": {"total": 100},
                            }
                        ],
                    }
                ]
            },
            "pi": {"disabled": True},
            "copilot_cli": {"disabled": True},
            "claude": {},
        }

        test_args = ["limitlens", "--usage", "--json", "--days", "7", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=config), \
             patch("limitlens.cli.get_codex_data", return_value={"accounts": []}), \
             patch("limitlens.cli.get_amp_data", return_value={}), \
             patch("limitlens.cli.get_antigravity_data", return_value={}), \
             patch("limitlens.cli.get_claude_data", return_value={}), \
             patch("limitlens.cli.get_opencode_data", return_value=observed) as mock_observed, \
             patch("limitlens.usage_tracker.waste_tracker._load_snapshots_with_anchor") as mock_snapshots, \
             patch("limitlens.usage_tracker.waste_tracker.compute_waste", return_value={}), \
             patch("limitlens.usage_tracker._load_imported_data", return_value={}), \
             redirect_stdout(buf):
            mock_snapshots.return_value = [
                {"tool": "codex", "key": "codex-default::weekly", "pct_left": 90.0, "_ts": now - timedelta(hours=2)},
                {"tool": "codex", "key": "codex-default::weekly", "pct_left": 70.0, "_ts": now - timedelta(hours=1)},
            ]
            main()

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["metadata"]["days"], 7)
        self.assertIn("generated_at", payload["metadata"])
        self.assertEqual(payload["snapshot_usage"]["codex-default::weekly"]["used"], 20.0)
        self.assertEqual(payload["snapshot_usage"]["codex-default::weekly"]["unit"], "percent")
        self.assertEqual(payload["waste"], {})
        self.assertEqual(payload["observed"], observed)
        self.assertEqual(payload["totals"]["observed"]["requests"], 1)
        curr_ts = now - timedelta(hours=1)
        self.assertEqual(payload["history"][curr_ts.strftime("%Y-%m-%d")], {"codex-default::weekly": 20.0})
        self.assertEqual(payload["consolidated_usage"], {"codex-default::weekly": 20.0})
        mock_observed.assert_called_once()

    def test_usage_command_alias_routes_to_usage_mode(self):
        config = dict(TEST_CONFIG)
        config["pi"] = {"enabled": False}
        config["cursor"] = {"enabled": False}
        analytics = {
            "metadata": {"days": 7, "generated_at": "2026-06-21T00:00:00+00:00"},
            "snapshot_usage": {},
            "history": {},
            "waste": {},
            "observed": {},
            "totals": {},
        }

        test_args = ["limitlens", "usage", "--json", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=config), \
             patch("limitlens.cli.get_codex_data", return_value={"accounts": []}), \
             patch("limitlens.cli.get_amp_data", return_value={}), \
             patch("limitlens.cli.get_antigravity_data", return_value={}), \
             patch("limitlens.cli.get_opencode_data", return_value={}), \
             patch("limitlens.cli.get_claude_data", return_value={}), \
             patch("limitlens.usage_tracker.compute_usage_analytics", return_value=analytics) as mock_analytics, \
             redirect_stdout(buf):
            main()

        self.assertEqual(json.loads(buf.getvalue())["version"], 4)
        mock_analytics.assert_called_once()

    def test_plain_usage_output_has_no_ansi_color(self):
        config = dict(TEST_CONFIG)
        config["pi"] = {"enabled": False}
        config["cursor"] = {"enabled": False}
        analytics = {
            "metadata": {"days": 7, "generated_at": "2026-06-21T00:00:00+00:00"},
            "snapshot_usage": {},
            "history": {},
            "waste": {},
            "observed": {},
            "totals": {},
        }

        test_args = ["limitlens", "usage", "--plain", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=config), \
             patch("limitlens.cli.get_codex_data", return_value={"accounts": []}), \
             patch("limitlens.cli.get_amp_data", return_value={}), \
             patch("limitlens.cli.get_antigravity_data", return_value={}), \
             patch("limitlens.cli.get_opencode_data", return_value={}), \
             patch("limitlens.cli.get_claude_data", return_value={}), \
             patch("limitlens.usage_tracker.compute_usage_analytics", return_value=analytics), \
             redirect_stdout(buf):
            main()

        self.assertNotIn("\033[", buf.getvalue())
        self.assertIn("No usage history recorded yet", buf.getvalue())

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": True},
        "amp": {"enabled": True},
        "antigravity": {"enabled": True},
        "opencode": {"enabled": False},
        "pi": {"enabled": False},
        "claude": {"enabled": False},
        "cursor": {"enabled": False},
        "pioneer": {"enabled": False},
        "commandcode": {"enabled": False},
        "custom_tools": {"enabled": False},
    })
    @patch("limitlens.cli.get_codex_data", return_value={"accounts": []})
    @patch("limitlens.cli.get_amp_data", return_value={})
    @patch("limitlens.cli.get_antigravity_data", return_value={"profiles": []})
    @patch("limitlens.waste_tracker.record_snapshot")
    @patch("limitlens.recommendations.display_recommendations")
    def test_suggest_command_alias_routes_to_recommendations(
        self, mock_display, mock_record, mock_ag, mock_amp, mock_codex, mock_config
    ):
        test_args = ["limitlens", "s", "--no-color"]
        with patch.object(sys, "argv", test_args), redirect_stdout(io.StringIO()):
            main()

        mock_display.assert_called_once()

    def test_short_s_is_not_reserved_for_suggest(self):
        test_args = ["limitlens", "-s"]
        with patch.object(sys, "argv", test_args), self.assertRaises(SystemExit):
            main()

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": False},
        "amp": {"enabled": True},
        "antigravity": {"enabled": False},
        "opencode": {"enabled": False},
        "pi": {"enabled": False},
        "claude": {"enabled": False},
        "cursor": {"enabled": False},
        "pioneer": {"enabled": False},
        "commandcode": {"enabled": False},
        "custom_tools": {"enabled": False},
    })
    @patch("limitlens.cli.get_amp_data", return_value={"error": "amp exploded"})
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_provider_error_visible_in_default_text_output(self, mock_record, mock_amp, mock_config):
        test_args = ["limitlens", "--no-color", "--no-record", "--no-recommend"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), redirect_stdout(buf):
            main()

        self.assertIn("Amp", buf.getvalue())
        self.assertIn("amp exploded", buf.getvalue())

    @patch("limitlens.cli.load_limitlens_config", return_value={"amp": {"enabled": False}})
    @patch("limitlens.cli.get_amp_data", return_value={"error": "disabled but requested"})
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_tool_specific_provider_runs_and_shows_error_when_disabled(self, mock_record, mock_amp, mock_config):
        test_args = ["limitlens", "--tool", "amp", "--no-color", "--no-record", "--no-recommend"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), redirect_stdout(buf):
            main()

        mock_amp.assert_called_once()
        self.assertIn("disabled but requested", buf.getvalue())

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_provider_failure_isolated_in_json(
        self, mock_record, mock_custom, mock_opencode, mock_ag, mock_amp, mock_codex
    ):
        mock_codex.return_value = {"accounts": []}
        mock_amp.side_effect = TypeError("bad local output")
        mock_ag.return_value = {"profiles": []}
        mock_opencode.return_value = {"opencode": {"windows": []}, "copilot_cli": {"disabled": True}}
        mock_custom.return_value = {"tools": []}

        test_args = ["limitlens", "--json", "--tool", "all", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()

        payload = json.loads(buf.getvalue())
        self.assertIn("amp provider failed: TypeError: bad local output", payload["amp"]["error"])
        self.assertIn("codex", payload)
        self.assertIn("antigravity", payload)

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": True},
        "pioneer": {"enabled": False},
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

    @patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG)
    @patch("limitlens.cli.get_pi_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_direct_pi_uses_top_level_provider(self, mock_record, mock_pi, mock_config):
        mock_pi.return_value = {"windows": [{"days": 1, "models": []}]}

        test_args = ["limitlens", "--json", "--tool", "pi", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), redirect_stdout(buf):
            main()

        mock_pi.assert_called_once()
        payload = json.loads(buf.getvalue())
        self.assertIn("pi", payload)
        self.assertNotIn("opencode", payload)

    @patch("limitlens.cli.load_limitlens_config", return_value={
        "codex": {"enabled": False},
        "pioneer": {"enabled": False},
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

    @patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG)
    @patch("limitlens.waste_tracker.reset_snapshots")
    @patch("limitlens.cli.print_c")
    def test_reset_waste(self, mock_print, mock_reset, mock_config):
        mock_reset.return_value = True

        test_args = ["limitlens", "--reset-waste"]
        with patch.object(sys, "argv", test_args):
            main()

        mock_reset.assert_called_once()
        # Verify success message was printed
        self.assertTrue(any("waste history cleared" in str(call) for call in mock_print.mock_calls))

    @patch("limitlens.providers.observed.mark_spend_reset", return_value=True)
    @patch("limitlens.cli.load_limitlens_config", return_value={
        "custom_tools": {"enabled": True, "tools": {"kilo": {}}},
    })
    def test_reset_spend_rewrites_custom_kilo_config(
        self, mock_config, mock_mark_reset
    ):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump({
                "custom_tools": {
                    "tools": {
                        "kilo": {"used": 8, "request_count": 3},
                        "other": {"used": 0, "request_count": 0},
                    }
                }
            }, f)
            config_path = f.name

        try:
            test_args = ["limitlens", "--reset-spend"]
            with patch.object(sys, "argv", test_args), \
                 patch("limitlens.cli.limitlens_config_path", return_value=config_path), \
                 redirect_stdout(io.StringIO()):
                main()

            extra_data = mock_mark_reset.call_args.kwargs["extra_data"]
            self.assertEqual(extra_data, {})

            with open(config_path, encoding="utf-8") as f:
                updated = json.load(f)
            self.assertEqual(updated["custom_tools"]["tools"]["kilo"]["used"], 0)
            self.assertEqual(updated["custom_tools"]["tools"]["kilo"]["request_count"], 0)
            self.assertEqual(updated["custom_tools"]["tools"]["other"]["used"], 0)
            self.assertEqual(updated["custom_tools"]["tools"]["other"]["request_count"], 0)
        finally:
            os.remove(config_path)

    @patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG)
    @patch("argparse.ArgumentParser.error")
    def test_invalid_interval(self, mock_error, mock_config):
        test_args = ["limitlens", "--interval", "0"]
        with patch.object(sys, "argv", test_args):
            main()
        mock_error.assert_called_once_with("--interval must be greater than 0")

    @patch("limitlens.cli.get_opencode_data")
    def test_opencode_exception(self, mock_opencode):
        mock_opencode.side_effect = Exception("test error")
        test_args = ["limitlens", "--json", "--tool", "opencode", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()
        payload = json.loads(buf.getvalue())
        self.assertIn("opencode provider failed: Exception: test error", payload["opencode"]["error"])

    @patch("limitlens.cli.get_commandcode_data")
    def test_commandcode_tool(self, mock_cc):
        mock_cc.return_value = {"cc": 1}
        test_args = ["limitlens", "--json", "--tool", "commandcode", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()
        payload = json.loads(buf.getvalue())
        self.assertIn("commandcode", payload)

    @patch("limitlens.providers.codex.refresh_all_accounts")
    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.get_codex_data")
    def test_sync_codex_prints(self, mock_codex, mock_print, mock_refresh_all):
        mock_codex.return_value = {"accounts": []}
        test_args = ["limitlens", "--sync-codex", "--tool", "codex", "--no-record", "--no-recommend"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()
        mock_refresh_all.assert_called_once()
        self.assertTrue(any("syncing codex accounts" in str(c) for c in mock_print.mock_calls))

    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.providers.codex.refresh_all_accounts")
    @patch("limitlens.cli.print_c")
    def test_refresh_codex_refreshes_and_exits(self, mock_print, mock_refresh_all, mock_codex):
        mock_refresh_all.return_value = {
            "default": {"ok": True, "error": None},
            "work": {"ok": False, "error": "timeout"},
        }
        test_args = ["limitlens", "--refresh-codex"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()
        mock_refresh_all.assert_called_once()
        # Should not perform a status fetch when refreshing and exiting.
        mock_codex.assert_not_called()
        self.assertTrue(any("default refreshed" in str(c) for c in mock_print.mock_calls))
        self.assertTrue(any("work failed: timeout" in str(c) for c in mock_print.mock_calls))

    @patch("limitlens.providers.codex.refresh_all_accounts", return_value={})
    @patch("limitlens.cli.get_codex_data")
    def test_refresh_codex_json(self, mock_codex, mock_refresh_all):
        test_args = ["limitlens", "--refresh-codex", "--json"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()) as buf:
            main()
        mock_refresh_all.assert_called_once()
        mock_codex.assert_not_called()
        self.assertEqual(json.loads(buf.getvalue()), {})

    @patch("limitlens.providers.codex.refresh_accounts")
    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.get_codex_data")
    def test_codex_auto_refresh_print_and_skip(self, mock_codex, mock_print, mock_refresh_accounts):
        mock_codex.side_effect = [{"accounts": [{"name": "skip_me", "home": "~", "limits": [{"is_stale": False}]}, {"name": "stale_me", "home": "~", "limits": [{"is_stale": True}]}]}, {"accounts": []}]
        test_args = ["limitlens", "--tool", "codex", "--no-record", "--no-recommend"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()
        mock_refresh_accounts.assert_called_once_with(["stale_me"], TEST_CONFIG)
        self.assertTrue(any("refreshing stale codex accounts" in str(c) for c in mock_print.mock_calls))

    @patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG)
    @patch("limitlens.waste_tracker.reset_snapshots", return_value=False)
    @patch("limitlens.cli.print_c")
    def test_reset_waste_failure(self, mock_print, mock_reset, mock_config):
        test_args = ["limitlens", "--reset-waste"]
        with patch.object(sys, "argv", test_args):
            main()
        self.assertTrue(any("failed to delete" in str(c) for c in mock_print.mock_calls))

    @patch("limitlens.waste_tracker.record_snapshot")
    @patch("limitlens.cli.get_codex_data")
    def test_record_only(self, mock_codex, mock_record):
        mock_codex.return_value = {}
        test_args = ["limitlens", "--record", "--tool", "codex"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG):
            main()
        mock_record.assert_called_once()

    @patch("limitlens.waste_tracker.compute_waste", return_value={"waste": 1})
    @patch("limitlens.cli.print_c")
    @patch("limitlens.providers.codex.refresh_all_accounts", side_effect=Exception("network error"))
    def test_waste_exception_and_json(self, mock_refresh_all, mock_print, mock_compute):
        test_args = ["limitlens", "--waste", "--tool", "codex", "--sync-codex"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             patch("limitlens.waste_tracker.display_waste_report"):
            main()
        self.assertTrue(any("live fetch failed" in str(c) for c in mock_print.mock_calls))

        test_args_json = ["limitlens", "--waste", "--json", "--tool", "codex", "--sync-codex"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args_json), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             patch("limitlens.waste_tracker.display_waste_report"), \
             redirect_stdout(buf):
            main()
        self.assertEqual(json.loads(buf.getvalue()), {"waste": 1})

    @patch("limitlens.recommendations.compute_recommendations", return_value={"recs": []})
    @patch("limitlens.recommendations.display_recommendations")
    @patch("limitlens.cli.get_codex_data", return_value={})
    def test_reco_only(self, mock_codex, mock_display_recs, mock_compute):
        test_args = ["limitlens", "--reco", "--tool", "codex", "--no-record"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG):
            main()
        mock_display_recs.assert_called_once()

        test_args_json = ["limitlens", "--reco", "--json", "--tool", "codex", "--no-record"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args_json), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(buf):
            main()
        self.assertEqual(json.loads(buf.getvalue()), {"recs": []})

    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.display_codex_text")
    @patch("limitlens.cli.display_amp_text")
    @patch("limitlens.cli.display_antigravity_text")
    @patch("limitlens.cli.display_pioneer_text")
    @patch("limitlens.cli.display_commandcode_text")
    @patch("limitlens.cli.display_custom_text")
    @patch("limitlens.cli.display_opencode_text")
    @patch("limitlens.cli.display_pi_text")
    @patch("limitlens.cli.display_cursor_text")
    @patch("limitlens.cli.display_at_glance")
    @patch("limitlens.recommendations.compute_recommendations")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.get_amp_data")
    @patch("limitlens.cli.get_antigravity_data")
    @patch("limitlens.cli.get_opencode_data")
    @patch("limitlens.cli.get_pi_data")
    @patch("limitlens.cli.get_pioneer_data")
    @patch("limitlens.cli.get_commandcode_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.cli.get_cursor_data")
    def test_display_all_providers(
        self, mock_g_cursor, mock_g_custom, mock_g_cc, mock_g_pioneer, mock_g_pi,
        mock_g_oc, mock_g_ag, mock_g_amp, mock_g_codex, mock_compute, mock_glance,
        mock_d_cursor, mock_d_pi, mock_d_oc, mock_d_custom, mock_d_cc,
        mock_d_pioneer, mock_d_ag, mock_d_amp, mock_d_codex, mock_print
    ):
        mock_g_codex.return_value = {"codex_active": True}
        mock_g_amp.return_value = {"amp_active": True}
        mock_g_ag.return_value = {"ag_active": True}
        mock_g_oc.return_value = {"oc_active": True}
        mock_g_pi.return_value = {"pi_active": True}
        mock_g_pioneer.return_value = {"pioneer_active": True}
        mock_g_cc.return_value = {"cc_active": True}
        mock_g_custom.return_value = {"custom_active": True}
        mock_g_cursor.return_value = {"cursor_active": True}
        mock_compute.return_value = {}

        test_config = {
            "codex": {"enabled": True}, "amp": {"enabled": True}, "antigravity": {"enabled": True},
            "opencode": {"enabled": True}, "pi": {"enabled": True}, "pioneer": {"enabled": True},
            "commandcode": {"enabled": True}, "custom_tools": {"enabled": True},
            "cursor": {"enabled": True}
        }
        test_args = ["limitlens", "--tool", "all", "--no-record"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=test_config), \
             redirect_stdout(io.StringIO()):
            main()

        mock_glance.assert_called_once()
        mock_d_codex.assert_called_once()
        mock_d_amp.assert_called_once()
        mock_d_ag.assert_called_once()
        mock_d_pioneer.assert_called_once()
        mock_d_cc.assert_called_once()
        mock_d_custom.assert_called_once()
        mock_d_oc.assert_called_once()
        mock_d_pi.assert_not_called()
        mock_d_cursor.assert_called_once()

    @patch("limitlens.cli.time.sleep", side_effect=KeyboardInterrupt)
    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.get_codex_data")
    def test_watch_mode(self, mock_codex, mock_print, mock_sleep):
        mock_codex.return_value = {"accounts": []}
        test_args = ["limitlens", "--watch", "--interval", "1", "--tool", "codex", "--no-record", "--no-recommend"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()
        mock_sleep.assert_called_once_with(1.0)
        self.assertTrue(any("refreshing every" in str(c) for c in mock_print.mock_calls))
        self.assertTrue(any("Press Ctrl+C to stop" in str(c) for c in mock_print.mock_calls))

    @patch("limitlens.cli.display_codex_text")
    @patch("limitlens.providers.codex.refresh_accounts")
    @patch("limitlens.cli.time.sleep")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.cli.time.monotonic", side_effect=[0, 400, 401, 402])
    def test_watch_mode_cooldown(self, mock_time, mock_codex, mock_sleep, mock_refresh_accounts, mock_display_codex):
        mock_codex.side_effect = [
            {"accounts": [{"name": "stale_me", "home": "~", "limits": [{"is_stale": True}]}, {"name": "not_stale", "home": "~", "limits": [{"is_stale": False}]}, {"home": "~", "limits": [{"is_stale": True}]}]}, # call 1
            {"accounts": [{"name": "stale_me", "home": "~", "limits": [{"is_stale": True}]}]}, # result of refresh
            {"accounts": [{"name": "stale_me", "home": "~", "limits": [{"is_stale": True}]}]}, # loop 2 (cooldown expired)
            {"accounts": [{"name": "stale_me", "home": "~", "limits": [{"is_stale": True}]}]}  # result of refresh
        ]

        def sleep_effect(*args):
            if mock_sleep.call_count == 2:
                raise KeyboardInterrupt
        mock_sleep.side_effect = sleep_effect

        test_args = ["limitlens", "--watch", "--interval", "1", "--tool", "codex", "--no-record", "--no-recommend"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()

        self.assertEqual(mock_refresh_accounts.call_count, 2)

    @patch("limitlens.cli.load_limitlens_config", side_effect=KeyboardInterrupt)
    def test_top_level_keyboard_interrupt(self, mock_load):
        test_args = ["limitlens"]
        with patch.object(sys, "argv", test_args):
            main()

    @patch("limitlens.cli.load_limitlens_config", side_effect=ValueError("Unexpected Error"))
    @patch("sys.exit")
    def test_top_level_exception(self, mock_exit, mock_load):
        test_args = ["limitlens"]
        with patch.object(sys, "argv", test_args), \
             redirect_stdout(io.StringIO()):
            main()
        mock_exit.assert_called_once_with(1)


    @patch("limitlens.providers.observed.mark_spend_reset", return_value=True)
    @patch("limitlens.cli.load_limitlens_config", return_value={
        "custom_tools": {"enabled": True},
    })
    def test_reset_spend_with_list_form_custom_tools(self, mock_config, mock_mark_reset):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump({
                "custom_tools": {
                    "tools": [
                        {"name": "t1", "used": 5, "request_count": 2},
                        {"name": "t2", "used": 10, "request_count": 4}
                    ]
                }
            }, f)
            config_path = f.name

        try:
            test_args = ["limitlens", "--reset-spend"]
            with patch.object(sys, "argv", test_args), \
                 patch("limitlens.cli.limitlens_config_path", return_value=config_path), \
                 redirect_stdout(io.StringIO()):
                main()

            with open(config_path, encoding="utf-8") as f:
                updated = json.load(f)
            self.assertEqual(updated["custom_tools"]["tools"][0]["used"], 0)
            self.assertEqual(updated["custom_tools"]["tools"][1]["used"], 0)
        finally:
            os.remove(config_path)

    @patch("limitlens.cli.print_c")
    @patch("limitlens.cli.display_pi_text")
    @patch("limitlens.cli.get_pi_data")
    def test_display_pi_only_when_opencode_disabled(self, mock_get_pi, mock_display_pi, mock_print):
        mock_get_pi.return_value = {"pi_active": True}
        test_config = {
            "opencode": {"enabled": False},
            "pi": {"enabled": True},
        }
        test_args = ["limitlens", "--tool", "all", "--no-record"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=test_config), \
             redirect_stdout(io.StringIO()):
            main()
        mock_display_pi.assert_called_once()

    @patch("limitlens.runner.collect_quota_data", return_value={})
    def test_run_subcommand_dry_run_routes_without_launching(self, mock_collect):
        test_args = ["limitlens", "run", "--dry-run", "Plan the migration"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value={"pi": {"enabled": True}}), \
             patch("limitlens.runner.subprocess.run") as mock_subprocess, \
             redirect_stdout(buf):
            main()

        self.assertIn("LimitLens runner", buf.getvalue())
        self.assertIn("Pi", buf.getvalue())
        mock_collect.assert_called_once()
        mock_subprocess.assert_not_called()

    @patch("limitlens.runner.collect_quota_data", return_value={})
    def test_run_subcommand_accepts_cline_tool(self, mock_collect):
        test_args = ["limitlens", "run", "--tool", "cline", "--dry-run", "Say hello"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value={"cline": {"enabled": True}}), \
             patch("limitlens.runner.subprocess.run") as mock_subprocess, \
             redirect_stdout(buf):
            main()

        self.assertIn("Cline CLI", buf.getvalue())
        self.assertIn("cline 'Say hello'", buf.getvalue())
        mock_collect.assert_called_once()
        mock_subprocess.assert_not_called()

    @patch("limitlens.cli.auto_detect_providers", return_value={})
    def test_init_config_writes_explicitly(self, mock_auto_detect):
        test_args = ["limitlens", "--init-config"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.limitlens_config_path", return_value="/tmp/limitlens-test-config.json"), \
             redirect_stdout(io.StringIO()):
            main()

        mock_auto_detect.assert_called_once_with("/tmp/limitlens-test-config.json", write=True, interactive=True)

    @patch("limitlens.cli.auto_detect_providers")
    @patch("limitlens.cli.get_codex_data")
    def test_doctor_prints_readiness_without_fetching_provider_data(self, mock_codex, mock_auto_detect):
        config = {
            "codex": {"enabled": True},
            "amp": {"enabled": True},
            "antigravity": {"enabled": False},
            "commandcode": {"enabled": False},
            "custom_tools": {"enabled": False},
            "pioneer": {"enabled": False},
            "opencode": {"enabled": False},
            "pi": {"enabled": False},
            "cursor": {"enabled": False},
            "cline": {"enabled": True},
        }
        detected = {
            "codex": {"enabled": True},
            "amp": {"enabled": False},
            "antigravity": {"enabled": True},
            "commandcode": {"enabled": False},
            "cline": {"enabled": True},
        }
        mock_auto_detect.return_value = detected

        test_args = ["limitlens", "doctor"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=config), \
             patch("limitlens.cli.limitlens_config_path", return_value="/tmp/limitlens-test-config.json"), \
             redirect_stdout(buf):
            main()

        output = buf.getvalue()
        self.assertIn("LimitLens Doctor", output)
        self.assertIn("Codex", output)
        self.assertIn("ready", output)
        self.assertIn("Amp", output)
        self.assertIn("enabled, not detected", output)
        self.assertIn("Antigravity", output)
        self.assertIn("detected, disabled", output)
        self.assertIn("Cline", output)
        self.assertIn("Next: run `limitlens`", output)
        self.assertNotIn("/tmp/limitlens-test-config.json", output)
        mock_auto_detect.assert_called_once_with("/tmp/limitlens-test-config.json", write=False, interactive=False)
        mock_codex.assert_not_called()

    @patch("limitlens.cli.auto_detect_providers")
    def test_doctor_report_outputs_sanitized_json(self, mock_auto_detect):
        config = {
            "codex": {"enabled": True},
            "amp": {"enabled": True},
            "antigravity": {"enabled": False},
            "commandcode": {"enabled": False},
            "custom_tools": {"enabled": False},
            "pioneer": {"enabled": False},
            "opencode": {"enabled": False},
            "pi": {"enabled": False},
            "cursor": {"enabled": False},
            "cline": {"enabled": True},
        }
        mock_auto_detect.return_value = {
            "codex": {"enabled": True},
            "antigravity": {"enabled": True},
            "cline": {"enabled": True},
        }

        test_args = ["limitlens", "doctor", "--report"]
        buf = io.StringIO()
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=config), \
             patch("limitlens.cli.limitlens_config_path", return_value="/private/user/.config/limitlens/config.json"), \
             redirect_stdout(buf):
            main()

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["version"], 1)
        self.assertIn("python", payload)
        self.assertIn("os", payload)
        self.assertEqual(payload["providers"]["codex"]["state"], "ready")
        self.assertEqual(payload["providers"]["antigravity"]["state"], "detected_disabled")
        self.assertEqual(payload["providers"]["cline"]["state"], "ready")
        self.assertNotIn("/private/user", buf.getvalue())
        self.assertNotIn("config.json", buf.getvalue())
        mock_auto_detect.assert_called_once_with("/private/user/.config/limitlens/config.json", write=False, interactive=False)

    def test_report_requires_doctor_command(self):
        test_args = ["limitlens", "--report"]
        with patch.object(sys, "argv", test_args), \
             patch("argparse.ArgumentParser.error") as mock_error:
            main()

        mock_error.assert_called_once_with("--report can only be used with `limitlens doctor`")


class TestCLIThreadPool(unittest.TestCase):
    @patch("limitlens.cli.ThreadPoolExecutor")
    def test_cli_threadpool_scaling(self, mock_executor):
        from limitlens.cli import _main
        test_args = ["limitlens", "--tool", "all", "--no-record"]
        test_config = {
            "codex": {"enabled": True},
            "amp": {"enabled": True},
            "antigravity": {"enabled": True},
            "opencode": {"enabled": True},
        }
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=test_config), \
             redirect_stdout(io.StringIO()):
            try:
                _main()
            except Exception:
                pass

        mock_executor.assert_called()
        _, kwargs = mock_executor.call_args
        self.assertEqual(kwargs.get("max_workers"), 16)


class TestCLIDebugAndLogging(unittest.TestCase):
    def test_cli_debug_flag_prints_traceback_and_logs(self):
        from limitlens.cli import main

        test_args = ["limitlens", "--debug"]
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "limitlens.log")
            err_output = io.StringIO()
            with patch.object(sys, "argv", test_args), \
                 patch.dict(os.environ, {"LIMITLENS_LOG_PATH": log_file}), \
                 patch("limitlens.cli._main", side_effect=ValueError("Test Debug Exception")), \
                 patch("sys.stderr", err_output), \
                 self.assertRaises(SystemExit) as cm:
                main()

            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Test Debug Exception", err_output.getvalue())
            self.assertIn("traceback", err_output.getvalue().lower())

            self.assertTrue(os.path.exists(log_file))
            with open(log_file, "r") as f:
                content = f.read()
                self.assertIn("Test Debug Exception", content)
                self.assertIn("Traceback", content)


if __name__ == "__main__":
    unittest.main()
