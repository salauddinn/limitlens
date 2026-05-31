#!/usr/bin/env python3
"""Tests for limitlens.providers.codex — session parsing, limits, log issues."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import argparse
from unittest.mock import patch

from limitlens.core import load_display_config
from limitlens.providers.codex import (
    get_session_mtime,
    parse_limits,
    parse_usage_limit_message,
    parse_request_error_message,
    window_key_and_label,
    get_codex_data
)


class TestSessionMtime(unittest.TestCase):
    def test_get_session_mtime_existing(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello")
            path = f.name
        try:
            mtime = get_session_mtime(path)
            self.assertIsNotNone(mtime)
            self.assertAlmostEqual(mtime, os.path.getmtime(path), places=3)
        finally:
            os.unlink(path)

    def test_get_session_mtime_missing(self):
        self.assertIsNone(get_session_mtime("/nonexistent/path/xyz"))


class TestParseLimits(unittest.TestCase):
    def test_parse_limits_legacy_format(self):
        now = datetime.now(timezone.utc)
        reset_time = (now + timedelta(hours=2)).isoformat()
        lines = [
            json.dumps({
                "limit_5h": {"used_percent": 30.0, "reset_time": reset_time},
                "limit_weekly": {"used_percent": 10.0, "reset_time": reset_time},
            }),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name
        try:
            limits, status, tokens = parse_limits(path)
            self.assertIn("5h", limits)
            self.assertIn("weekly", limits)
            self.assertEqual(limits["5h"]["used_percent"], 30.0)
        finally:
            os.unlink(path)

    def test_parse_limits_rate_limit_format(self):
        now = datetime.now(timezone.utc)
        reset_time = (now + timedelta(hours=2)).isoformat()
        lines = [
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 100}},
                    "rate_limits": {
                        "primary": {
                            "window_minutes": 300,
                            "used_percent": 25.0,
                            "resets_at": reset_time,
                        }
                    }
                }
            }),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for line in lines:
                f.write(line + "\n")
            path = f.name
        try:
            limits, status, tokens = parse_limits(path)
            self.assertIn("5h", limits)
            self.assertEqual(limits["5h"]["used_percent"], 25.0)
        finally:
            os.unlink(path)



class TestCodexParsing(unittest.TestCase):
    def test_parse_usage_limit_message(self):
        self.assertEqual(
            parse_usage_limit_message("usage_limit_exceeded"),
            "usage limit reached"
        )
        msg = "usage_limit_exceeded: try again at 12:00 PM."
        self.assertEqual(
            parse_usage_limit_message(msg),
            "usage limited until 12:00 PM"
        )

    def test_parse_request_error_message(self):
        self.assertEqual(
            parse_request_error_message('{"message":"invalid token"}'),
            "invalid token"
        )
        self.assertIsNone(parse_request_error_message("random string"))

    def test_window_key_and_label(self):
        self.assertEqual(window_key_and_label(300), ("5h", "5h window"))
        self.assertEqual(window_key_and_label(10080), ("weekly", "weekly"))
        self.assertEqual(window_key_and_label(None), ("unknown", "unknown"))
        self.assertEqual(window_key_and_label(1440), ("1440m", "1d window"))
        self.assertEqual(window_key_and_label(120), ("120m", "2h window"))
        self.assertEqual(window_key_and_label(45), ("45m", "45m window"))


class TestGetCodexData(unittest.TestCase):
    @patch("limitlens.providers.codex.discover_accounts")
    def test_get_codex_data_no_accounts(self, mock_discover):
        mock_discover.return_value = {}
        args = argparse.Namespace(redact=False)
        res = get_codex_data(args)
        self.assertEqual(res["error"], "no codex accounts found (~/.codex-*)")

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.load_display_config")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_get_codex_data_with_account(
        self, mock_exists, mock_load_disp, mock_find_log, mock_parse, mock_mtime, mock_latest, mock_discover
    ):
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/fake/home"}
        mock_latest.return_value = "/fake/home/sessions/rollout-1.jsonl"
        # Use a recent mtime (1 minute ago) so staleness check doesn't trigger
        now_ts = datetime.now(timezone.utc).timestamp()
        mock_mtime.return_value = now_ts - 60
        mock_parse.return_value = (
            {
                "5h": {
                    "key": "5h",
                    "label": "5h window",
                    "window_minutes": 300,
                    "used_percent": 20.0,
                    "reset_time": None
                }
            },
            None,
            None
        )
        mock_find_log.return_value = None
        mock_load_disp.return_value = {"auto_hide_enabled": False}

        args = argparse.Namespace(redact=False)
        res = get_codex_data(args)
        
        accounts = res.get("accounts", [])
        self.assertEqual(len(accounts), 1)
        acc = accounts[0]
        self.assertEqual(acc["name"], "default")
        self.assertIn("limits", acc)
        self.assertEqual(acc["limits"][0]["label"], "5h window")
        self.assertEqual(acc["limits"][0]["left_percent"], 80.0)
        self.assertFalse(acc["limits"][0]["is_stale"])


class TestStalenessDetection(unittest.TestCase):
    """Tests for the session-age staleness detection in get_codex_data."""

    def _make_args(self, redact=False):
        return argparse.Namespace(redact=redact)

    def _limits_with_high_usage(self, reset_time=None):
        """Return limits dict mimicking 97% usage (the bug scenario)."""
        return {
            "5h": {
                "key": "5h",
                "label": "5h window",
                "window_minutes": 300,
                "used_percent": 97.0,
                "reset_time": reset_time,
            }
        }

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.load_display_config")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_stale_session_older_than_window(
        self, mock_exists, mock_load_disp, mock_find_log, mock_parse,
        mock_mtime, mock_latest, mock_discover,
    ):
        """Session is 8 hours old, 5h window → data is stale, should show 100% left."""
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/fake/home"}
        mock_latest.return_value = "/fake/home/sessions/rollout-1.jsonl"
        # Session file last modified 8 hours ago
        now_ts = datetime.now(timezone.utc).timestamp()
        mock_mtime.return_value = now_ts - (8 * 3600)
        # reset_time is far in the future (e.g. weekly reset)
        future_reset = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        mock_parse.return_value = (self._limits_with_high_usage(reset_time=future_reset), None, None)
        mock_find_log.return_value = None
        mock_load_disp.return_value = {"auto_hide_enabled": False}

        res = get_codex_data(self._make_args())
        lim = res["accounts"][0]["limits"][0]

        self.assertEqual(lim["left_percent"], 100.0)
        self.assertEqual(lim["used_percent"], 0.0)
        self.assertTrue(lim["is_stale"])
        self.assertIn("stale", lim["reset_time_fmt"])

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.load_display_config")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_fresh_session_within_window(
        self, mock_exists, mock_load_disp, mock_find_log, mock_parse,
        mock_mtime, mock_latest, mock_discover,
    ):
        """Session is 1 hour old, 5h window → NOT stale, show actual usage."""
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/fake/home"}
        mock_latest.return_value = "/fake/home/sessions/rollout-1.jsonl"
        # Session file last modified 1 hour ago (within the 5h window)
        now_ts = datetime.now(timezone.utc).timestamp()
        mock_mtime.return_value = now_ts - (1 * 3600)
        future_reset = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        mock_parse.return_value = (self._limits_with_high_usage(reset_time=future_reset), None, None)
        mock_find_log.return_value = None
        mock_load_disp.return_value = {"auto_hide_enabled": False}

        res = get_codex_data(self._make_args())
        lim = res["accounts"][0]["limits"][0]

        # Data is fresh — should show the actual 97% usage
        self.assertEqual(lim["left_percent"], 3.0)
        self.assertEqual(lim["used_percent"], 97.0)
        self.assertFalse(lim["is_stale"])

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.load_display_config")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_stale_session_no_reset_time(
        self, mock_exists, mock_load_disp, mock_find_log, mock_parse,
        mock_mtime, mock_latest, mock_discover,
    ):
        """Session is 8 hours old, reset_time is None → staleness triggers via age check."""
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/fake/home"}
        mock_latest.return_value = "/fake/home/sessions/rollout-1.jsonl"
        now_ts = datetime.now(timezone.utc).timestamp()
        mock_mtime.return_value = now_ts - (8 * 3600)
        # No reset_time — is_reset_passed would return False,
        # but session-age check should still catch it
        mock_parse.return_value = (self._limits_with_high_usage(reset_time=None), None, None)
        mock_find_log.return_value = None
        mock_load_disp.return_value = {"auto_hide_enabled": False}

        res = get_codex_data(self._make_args())
        lim = res["accounts"][0]["limits"][0]

        self.assertEqual(lim["left_percent"], 100.0)
        self.assertEqual(lim["used_percent"], 0.0)
        self.assertTrue(lim["is_stale"])
        self.assertIn("stale", lim["reset_time_fmt"])


if __name__ == "__main__":
    unittest.main()
