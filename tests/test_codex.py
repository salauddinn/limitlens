#!/usr/bin/env python3
"""Tests for limitlens.providers.codex — session parsing, limits, log issues."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from limitlens.providers.codex import get_session_mtime, parse_limits


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


if __name__ == "__main__":
    unittest.main()
