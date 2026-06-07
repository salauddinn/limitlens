#!/usr/bin/env python3
"""Tests for limitlens.cli."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from limitlens.cli import main


TEST_CONFIG = {
    "codex": {"enabled": True, "auto_refresh": True},
    "opencode": {"enabled": True},
    "pi": {"enabled": True},
    "pioneer": {"enabled": False},
    "agentrouter": {"enabled": False},
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

        test_args = ["limitlens", "--json", "--tool", "codex", "--sync-codex", "--no-record"]
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
             redirect_stdout(io.StringIO()):
            main()

        mock_refresh_all.assert_called_once()
        mock_print.assert_not_called()

    @patch("limitlens.providers.codex.refresh_accounts")
    @patch("limitlens.cli.get_codex_data")
    @patch("limitlens.waste_tracker.record_snapshot")
    def test_stale_codex_refreshes_by_default(self, mock_record, mock_codex, mock_refresh_accounts):
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
        with patch.object(sys, "argv", test_args), \
             patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG):
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

    @patch("limitlens.cli.get_agentrouter_data")
    @patch("limitlens.cli.get_commandcode_data")
    def test_agentrouter_and_commandcode(self, mock_cc, mock_ar):
        mock_ar.return_value = {"ar": 1}
        mock_cc.return_value = {"cc": 1}
        for tool in ["agentrouter", "commandcode"]:
            test_args = ["limitlens", "--json", "--tool", tool, "--no-record"]
            buf = io.StringIO()
            with patch.object(sys, "argv", test_args), \
                 patch("limitlens.cli.load_limitlens_config", return_value=TEST_CONFIG), \
                 redirect_stdout(buf):
                main()
            payload = json.loads(buf.getvalue())
            if tool == "agentrouter":
                self.assertIn("agentrouter", payload)
            else:
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
    @patch("limitlens.cli.display_agentrouter_text")
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
    @patch("limitlens.cli.get_agentrouter_data")
    @patch("limitlens.cli.get_commandcode_data")
    @patch("limitlens.cli.get_custom_data")
    @patch("limitlens.cli.get_cursor_data")
    def test_display_all_providers(
        self, mock_g_cursor, mock_g_custom, mock_g_cc, mock_g_ar, mock_g_pioneer, mock_g_pi,
        mock_g_oc, mock_g_ag, mock_g_amp, mock_g_codex, mock_compute, mock_glance,
        mock_d_cursor, mock_d_pi, mock_d_oc, mock_d_custom, mock_d_cc, mock_d_ar,
        mock_d_pioneer, mock_d_ag, mock_d_amp, mock_d_codex, mock_print
    ):
        mock_g_codex.return_value = {"codex_active": True}
        mock_g_amp.return_value = {"amp_active": True}
        mock_g_ag.return_value = {"ag_active": True}
        mock_g_oc.return_value = {"oc_active": True}
        mock_g_pi.return_value = {"pi_active": True}
        mock_g_pioneer.return_value = {"pioneer_active": True}
        mock_g_ar.return_value = {"ar_active": True}
        mock_g_cc.return_value = {"cc_active": True}
        mock_g_custom.return_value = {"custom_active": True}
        mock_g_cursor.return_value = {"cursor_active": True}
        mock_compute.return_value = {}

        test_config = {
            "codex": {"enabled": True}, "amp": {"enabled": True}, "antigravity": {"enabled": True},
            "opencode": {"enabled": True}, "pi": {"enabled": True}, "pioneer": {"enabled": True},
            "agentrouter": {"enabled": True}, "commandcode": {"enabled": True}, "custom_tools": {"enabled": True},
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
        mock_d_ar.assert_called_once()
        mock_d_cc.assert_called_once()
        mock_d_custom.assert_called_once()
        mock_d_oc.assert_called_once()
        mock_d_pi.assert_called_once()
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



if __name__ == "__main__":
    unittest.main()
