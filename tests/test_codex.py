#!/usr/bin/env python3
"""Tests for limitlens.providers.codex — session parsing, limits, log issues."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import argparse
import sqlite3
import subprocess
from unittest.mock import patch

from limitlens.providers.codex import (
    get_session_mtime,
    parse_limits,
    parse_usage_limit_message,
    parse_request_error_message,
    window_key_and_label,
    get_codex_data,
    filter_accounts,
    discover_accounts,
    find_latest_session,
    get_session_files_since,
    parse_session_tokens,
    find_log_issue_in_sqlite,
    find_log_issue_in_text,
    display_codex_text,
    refresh_account,
    refresh_accounts,
    refresh_all_accounts,
    account_is_ignored,
    find_log_issue,
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

    def test_parse_limits_tolerates_null_used_percent(self):
        lines = [
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {
                            "window_minutes": 300,
                            "used_percent": None,
                            "resets_at": None,
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
            self.assertEqual(limits["5h"]["used_percent"], 0.0)
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
    def test_filter_accounts_ignores_default_codex(self):
        accounts = {
            "default": os.path.expanduser("~/.codex"),
            "p1": os.path.expanduser("~/.codex-p1"),
        }

        filtered = filter_accounts(accounts, {"codex": {"ignored_accounts": ["default"]}})

        self.assertNotIn("default", filtered)
        self.assertIn("p1", filtered)

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



class TestDiscoverAccounts(unittest.TestCase):
    @patch("limitlens.providers.codex.Path.home")
    def test_discover_accounts(self, mock_home):
        import pathlib
        
        with tempfile.TemporaryDirectory() as td:
            mock_home.return_value = pathlib.Path(td)
            
            (pathlib.Path(td) / ".codex").mkdir()
            (pathlib.Path(td) / ".codex-p1").mkdir()
            (pathlib.Path(td) / ".codex-file").touch()
            
            accounts = discover_accounts()
            self.assertIn("default", accounts)
            self.assertIn("p1", accounts)
            self.assertNotIn("file", accounts)
            self.assertEqual(accounts["default"], str(pathlib.Path(td) / ".codex"))
            self.assertEqual(accounts["p1"], str(pathlib.Path(td) / ".codex-p1"))

class TestFindLatestSession(unittest.TestCase):
    def test_find_latest_session(self):
        import time
        with tempfile.TemporaryDirectory() as td:
            sessions = os.path.join(td, "sessions")
            os.makedirs(sessions)
            f1 = os.path.join(sessions, "rollout-1.jsonl")
            f2 = os.path.join(sessions, "rollout-2.jsonl")
            open(f1, "w").close()
            time.sleep(0.01)
            open(f2, "w").close()
            
            latest = find_latest_session(td)
            self.assertEqual(latest, f2)
            
            since = os.path.getmtime(f1) - 1
            files = get_session_files_since(td, since)
            self.assertEqual(len(files), 2)
            self.assertIn(f1, files)
            self.assertIn(f2, files)

class TestParseSessionTokens(unittest.TestCase):
    def test_parse_session_tokens(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15
                        }
                    }
                }
            }) + "\n")
            f.write('{"type": "event_msg", "payload": {"type": "token_count", "info": {}}}\n')
            f.write("invalid json\n")
            f.write(json.dumps({"type": "other"}) + "\n")
            path = f.name
            
        try:
            tokens = parse_session_tokens(path)
            self.assertIsNotNone(tokens)
            self.assertEqual(tokens["input_tokens"], 10)
        finally:
            os.unlink(path)
            
    def test_parse_session_tokens_missing(self):
        self.assertIsNone(parse_session_tokens("/nonexistent/file.jsonl"))

class TestFindLogIssue(unittest.TestCase):
    def test_find_log_issue_in_text(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = os.path.join(td, "log")
            os.makedirs(log_dir)
            log_file = os.path.join(log_dir, "codex-tui.log")
            with open(log_file, "w") as f:
                f.write('{"message":"invalid_request_error: not supported"} \n')
                f.write('usage_limit_exceeded: try again at 12:00 PM.\n')
            
            issue = find_log_issue_in_text(td)
            self.assertEqual(issue, "usage limited until 12:00 PM")
            
            with open(log_file, "w") as f:
                f.write('{"message":"invalid_request_error"} \n')
            
            issue2 = find_log_issue_in_text(td)
            self.assertEqual(issue2, "invalid_request_error")

    @patch("limitlens.providers.codex.sqlite3.connect")
    def test_find_log_issue_in_sqlite_mocked(self, mock_connect):
        mock_conn = mock_connect.return_value
        mock_conn.execute.return_value.fetchone.return_value = ["usage_limit_exceeded: try again at 12:00 PM."]
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "logs_2.sqlite")
            open(db_path, "w").close()
            issue = find_log_issue_in_sqlite(td)
            self.assertEqual(issue, "usage limited until 12:00 PM")

    def test_find_log_issue_in_sqlite_missing(self):
        self.assertIsNone(find_log_issue_in_sqlite("/nonexistent/td"))

class TestDisplayCodexText(unittest.TestCase):
    @patch("limitlens.providers.codex.print_c")
    @patch("builtins.print")
    def test_display_codex_text(self, mock_print, mock_print_c):
        args = argparse.Namespace(no_color=True, verbose=True, all=False, tool=None)
        
        display_codex_text({"error": "all codex accounts are ignored by config"}, args)
        
        data = {
            "accounts": [
                {
                    "name": "default",
                    "home": "/home/codex",
                    "limits": [
                        {
                            "visible": True,
                            "label": "5h window",
                            "used_percent": 20.0,
                            "left_percent": 80.0,
                            "reset_time_fmt": "in 1h",
                            "is_stale": False
                        }
                    ],
                    "tokens": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "total_tokens": 30
                    }
                },
                {
                    "name": "p1",
                    "home": "/home/codex-p1",
                    "error": "folder not found"
                }
            ]
        }
        display_codex_text(data, args)
        mock_print.assert_any_call("\n  codex  /home/codex")

class TestRefreshAccounts(unittest.TestCase):
    @patch("limitlens.providers.codex.subprocess.run")
    @patch("limitlens.providers.codex.shutil.which")
    def test_refresh_account(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/codex"
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        
        ok, err = refresh_account("/home/codex")
        self.assertTrue(ok)
        self.assertIsNone(err)
        
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="codex", timeout=30)
        ok, err = refresh_account("/home/codex")
        self.assertFalse(ok)
        self.assertEqual(err, "timeout")

    @patch("limitlens.providers.codex.subprocess.run")
    @patch("limitlens.providers.codex.shutil.which")
    def test_refresh_account_nonzero_returncode(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/codex"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="auth error",
        )
        ok, err = refresh_account("/home/codex")
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        self.assertIn("1", str(err))

    @patch("limitlens.providers.codex.subprocess.run")
    @patch("limitlens.providers.codex.shutil.which")
    def test_refresh_account_zero_returncode_succeeds(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/codex"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        ok, err = refresh_account("/home/codex")
        self.assertTrue(ok)
        self.assertIsNone(err)

    @patch("limitlens.providers.codex.refresh_account")
    @patch("limitlens.providers.codex.discover_accounts")
    def test_refresh_all_accounts(self, mock_discover, mock_refresh):
        mock_discover.return_value = {"default": "/home/codex"}
        mock_refresh.return_value = (True, None)
        
        results = refresh_all_accounts()
        self.assertIn("default", results)
        self.assertTrue(results["default"]["ok"])

    @patch("limitlens.providers.codex.refresh_account")
    @patch("limitlens.providers.codex.discover_accounts")
    def test_refresh_accounts(self, mock_discover, mock_refresh):
        mock_discover.return_value = {"default": "/home/codex", "p1": "/home/codex-p1"}
        mock_refresh.return_value = (True, None)
        
        results = refresh_accounts(["p1"])
        self.assertIn("p1", results)
        self.assertNotIn("default", results)
        self.assertTrue(results["p1"]["ok"])

class TestGetCodexDataWeekly(unittest.TestCase):
    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.load_display_config")
    @patch("limitlens.providers.codex.os.path.exists")
    @patch("limitlens.providers.codex.get_session_files_since")
    @patch("limitlens.providers.codex.parse_session_tokens")
    def test_get_codex_data_weekly_tokens(
        self, mock_parse_tokens, mock_files_since, mock_exists, mock_load_disp, mock_find_log, mock_parse, mock_mtime, mock_latest, mock_discover
    ):
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/fake/home"}
        mock_latest.return_value = "/fake/home/sessions/rollout-1.jsonl"
        now_ts = datetime.now(timezone.utc).timestamp()
        mock_mtime.return_value = now_ts
        mock_parse.return_value = (
            {
                "weekly": {
                    "key": "weekly",
                    "label": "weekly",
                    "window_minutes": 10080,
                    "used_percent": 20.0,
                    "reset_time": "2030-01-01T00:00:00Z"
                }
            },
            None,
            None
        )
        mock_find_log.return_value = None
        mock_load_disp.return_value = {"auto_hide_enabled": False}
        
        mock_files_since.return_value = ["/fake/home/sessions/rollout-1.jsonl"]
        mock_parse_tokens.return_value = {
            "input_tokens": 10,
            "cached_input_tokens": 5,
            "output_tokens": 20,
            "reasoning_output_tokens": 2,
            "total_tokens": 30
        }
        
        args = argparse.Namespace(redact=False)
        res = get_codex_data(args)
        
        accounts = res.get("accounts", [])
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["tokens"]["total_tokens"], 30)


class TestCodexEdgeCases(unittest.TestCase):
    def test_account_is_ignored(self):
        self.assertTrue(account_is_ignored("p1", "/home/codex-p1", "p1"))
        self.assertFalse(account_is_ignored("p1", "/home/codex-p1", None))
        self.assertFalse(account_is_ignored("p1", "/home/codex-p1", "p2"))

    def test_parse_usage_limit_message_none(self):
        self.assertEqual(parse_usage_limit_message(None), "usage limit reached")
        self.assertEqual(parse_usage_limit_message("invalid format message"), "usage limit reached")

    def test_parse_request_error_message_none(self):
        self.assertIsNone(parse_request_error_message(None))

    @patch("limitlens.providers.codex.sqlite3.connect")
    def test_find_log_issue_in_sqlite_error(self, mock_connect):
        mock_connect.side_effect = sqlite3.Error("fake error")
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "logs_2.sqlite")
            open(db_path, "w").close()
            issue = find_log_issue_in_sqlite(td)
            self.assertEqual(issue, "Database error: fake error")

    @patch("limitlens.providers.codex.sqlite3.connect")
    def test_find_log_issue_in_sqlite_request_error(self, mock_connect):
        mock_conn = mock_connect.return_value
        mock_conn.execute.return_value.fetchone.return_value = ['{"message":"invalid_request_error: bad token"}']
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "logs_2.sqlite")
            open(db_path, "w").close()
            issue = find_log_issue_in_sqlite(td)
            self.assertEqual(issue, "invalid_request_error: bad token")
            
        # and no match branch
        mock_conn.execute.return_value.fetchone.return_value = None
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "logs_2.sqlite")
            open(db_path, "w").close()
            issue = find_log_issue_in_sqlite(td)
            self.assertIsNone(issue)

    def test_find_log_issue_in_text_error(self):
        with tempfile.TemporaryDirectory() as td:
            log_dir = os.path.join(td, "log")
            os.makedirs(log_dir)
            log_file = os.path.join(log_dir, "codex-tui.log")
            # Create a directory where file should be to cause OSError
            os.makedirs(log_file)
            issue = find_log_issue_in_text(td)
            self.assertTrue(issue.startswith("Log read error"))

    @patch("limitlens.providers.codex.find_log_issue_in_sqlite")
    @patch("limitlens.providers.codex.find_log_issue_in_text")
    def test_find_log_issue(self, mock_text, mock_sqlite):
        mock_sqlite.return_value = None
        mock_text.return_value = "issue"
        self.assertEqual(find_log_issue("/home"), "issue")

    @patch("limitlens.providers.codex.discover_accounts")
    def test_get_codex_data_all_ignored(self, mock_discover):
        mock_discover.return_value = {"default": "/home"}
        res = get_codex_data(argparse.Namespace(redact=False), {"codex": {"ignored_accounts": ["default"]}})
        self.assertEqual(res["error"], "all codex accounts are ignored by config")
        
    @patch("limitlens.providers.codex.discover_accounts")
    def test_get_codex_data_folder_not_found(self, mock_discover):
        mock_discover.return_value = {"default": "/nonexistent/path"}
        res = get_codex_data(argparse.Namespace(redact=False))
        self.assertEqual(res["accounts"][0]["error"], "folder not found")

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_get_codex_data_no_session(self, mock_exists, mock_issue, mock_latest, mock_discover):
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/home"}
        mock_latest.return_value = None
        mock_issue.return_value = None
        res = get_codex_data(argparse.Namespace(redact=False))
        self.assertEqual(res["accounts"][0]["error"], "no sessions yet — run codex once to populate")

        mock_issue.return_value = "some issue"
        res = get_codex_data(argparse.Namespace(redact=False))
        self.assertEqual(res["accounts"][0]["error"], "some issue")

    @patch("limitlens.providers.codex.discover_accounts")
    @patch("limitlens.providers.codex.find_latest_session")
    @patch("limitlens.providers.codex.get_session_mtime")
    @patch("limitlens.providers.codex.parse_limits")
    @patch("limitlens.providers.codex.find_log_issue")
    @patch("limitlens.providers.codex.os.path.exists")
    def test_get_codex_data_no_limits(self, mock_exists, mock_issue, mock_parse, mock_mtime, mock_latest, mock_discover):
        mock_exists.return_value = True
        mock_discover.return_value = {"default": "/home"}
        mock_latest.return_value = "/home/sessions/rollout-1.jsonl"
        mock_mtime.return_value = datetime.now(timezone.utc).timestamp()
        mock_parse.return_value = ({}, None, None)
        mock_issue.return_value = None
        res = get_codex_data(argparse.Namespace(redact=False))
        self.assertEqual(res["accounts"][0]["error"], "no rate limit data in sessions yet")

    def test_parse_limits_json_error(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("invalid json\n")
            f.write(json.dumps({"type": "event_msg", "payload": {"type": "error"}}) + "\n")
            path = f.name
        try:
            limits, status, tokens = parse_limits(path)
            self.assertEqual(limits, {})
            self.assertIsNone(status)
        finally:
            os.unlink(path)

    @patch("limitlens.providers.codex.print_c")
    @patch("builtins.print")
    def test_display_codex_text_branches(self, mock_print, mock_print_c):
        args = argparse.Namespace(no_color=False, verbose=True, all=False, tool=None)
        
        # Ignored
        display_codex_text({"error": "all codex accounts are ignored by config"}, args)
        
        # Stale and colored
        data = {
            "accounts": [
                {
                    "name": "default",
                    "home": "/home",
                    "last_updated": "2023-01-01T00:00:00Z",
                    "limits": [
                        {
                            "visible": True,
                            "label": "5h window",
                            "used_percent": 20.0,
                            "left_percent": 80.0,
                            "reset_time_fmt": "in 1h",
                            "is_stale": True
                        }
                    ]
                },
                {
                    "name": "p1",
                    "home": "/home",
                    "error": "✖ bad"
                }
            ]
        }
        display_codex_text(data, args)

        mock_print.reset_mock()
        split_data = {
            "accounts": [
                {
                    "name": "p1",
                    "home": "/home",
                    "limits": [
                        {"visible": True, "label": "5h window", "used_percent": 8.0, "left_percent": 92.0, "reset_time_fmt": "4 hours left", "is_stale": False},
                        {"visible": True, "label": "weekly", "used_percent": 10.0, "left_percent": 90.0, "reset_time_fmt": "6 days left", "is_stale": False},
                    ],
                }
            ]
        }
        display_codex_text(split_data, argparse.Namespace(no_color=True, verbose=False, all=False, tool=None))
        quota_lines = [call.args[0] for call in mock_print.call_args_list if call.args and "quota" in call.args[0]]
        self.assertEqual(len(quota_lines), 2)
        self.assertTrue(any("5h" in line and "92%" in line for line in quota_lines))
        self.assertTrue(any("weekly" in line and "90%" in line for line in quota_lines))
        
        # Test missing path refresh
        with patch("limitlens.providers.codex.shutil.which", return_value=None):
            ok, err = refresh_account("/home")
            self.assertFalse(ok)
            self.assertEqual(err, "codex not found on PATH")

        # Test OSError refresh
        with patch("limitlens.providers.codex.shutil.which", return_value="/usr/bin/codex"):
            with patch("limitlens.providers.codex.subprocess.run", side_effect=OSError("denied")):
                ok, err = refresh_account("/home")
                self.assertFalse(ok)
                self.assertEqual(err, "denied")

    @patch("limitlens.providers.codex.discover_accounts")
    def test_refresh_all_accounts_none(self, mock_discover):
        mock_discover.return_value = {}
        self.assertEqual(refresh_all_accounts(), {})
        self.assertEqual(refresh_accounts(["default"]), {})

if __name__ == "__main__":
    unittest.main()
