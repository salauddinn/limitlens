#!/usr/bin/env python3
"""Tests for limitlens.core — redaction, formatters, bar, config."""

import os
import unittest
from datetime import datetime, timedelta, timezone

from limitlens.core import (
    redact_email,
    redact_path,
    redact_text,
    bar,
    format_date_pretty,
    format_timestamp,
    humanize_remaining,
    fmt_reset,
    _fmt_tokens,
    deep_merge,
    configured_days,
    load_display_config,
)


class TestRedaction(unittest.TestCase):
    def test_redact_email_long_user(self):
        self.assertEqual(redact_email("alice@example.com"), "al***@example.com")

    def test_redact_email_short_user(self):
        self.assertEqual(redact_email("ab@example.com"), "***@example.com")

    def test_redact_email_invalid(self):
        self.assertEqual(redact_email("notanemail"), "notanemail")
        self.assertIsNone(redact_email(None))

    def test_redact_path_home(self):
        home = os.path.expanduser("~")
        self.assertEqual(redact_path(os.path.join(home, "foo")), os.path.join("~", "foo"))

    def test_redact_path_codex(self):
        self.assertEqual(
            redact_path("/tmp/.codex-myaccount/sessions"),
            "/tmp/.codex-***/sessions",
        )

    def test_redact_text_email_and_home(self):
        home = os.path.expanduser("~")
        text = f"user alice@example.com lives in {home}/work"
        out = redact_text(text)
        self.assertIn("al***@example.com", out)
        self.assertIn("~/work", out)


class TestFormatters(unittest.TestCase):
    def test_format_date_pretty_suffix(self):
        dt = datetime(2026, 5, 1, 14, 30)
        self.assertEqual(format_date_pretty(dt), "May 1st 2:30 PM")
        self.assertEqual(format_date_pretty(datetime(2026, 5, 2, 9, 5)), "May 2nd 9:05 AM")
        self.assertEqual(format_date_pretty(datetime(2026, 5, 3, 0, 0)), "May 3rd 12:00 AM")
        self.assertEqual(format_date_pretty(datetime(2026, 5, 4, 23, 0)), "May 4th 11:00 PM")
        self.assertEqual(format_date_pretty(datetime(2026, 5, 11, 12, 0)), "May 11th 12:00 PM")
        self.assertEqual(format_date_pretty(datetime(2026, 5, 21, 12, 0)), "May 21st 12:00 PM")

    def test_format_timestamp_returns_string(self):
        dt = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
        out = format_timestamp(dt)
        self.assertIsInstance(out, str)
        self.assertIn("2026", out)

    def test_humanize_remaining_shows_minutes_under_three_hours(self):
        self.assertEqual(humanize_remaining(timedelta(hours=2, minutes=34)), "2h 34m")
        self.assertEqual(humanize_remaining(timedelta(minutes=42)), "42m")

    def test_humanize_remaining_stays_coarse_after_three_hours(self):
        self.assertEqual(humanize_remaining(timedelta(hours=4, minutes=45)), "4 hours")

    def test_fmt_reset_empty(self):
        self.assertEqual(fmt_reset(None), "—")
        self.assertEqual(fmt_reset(""), "—")

    def test_fmt_reset_future(self):
        future = datetime.now(timezone.utc) + timedelta(days=2)
        out = fmt_reset(future.isoformat())
        self.assertIn("left to reset", out)

    def test_fmt_reset_past(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(fmt_reset(past.isoformat()), "resetting soon")

    def test_fmt_reset_past_stale(self):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(fmt_reset(past.isoformat(), is_stale=True), "likely reset (open to refresh)")

    def test_fmt_reset_unix_timestamp_future(self):
        future_ts = (datetime.now(timezone.utc) + timedelta(days=1)).timestamp()
        out = fmt_reset(future_ts)
        self.assertIn("left to reset", out)


class TestFmtTokens(unittest.TestCase):
    def test_millions(self):
        self.assertEqual(_fmt_tokens(1_500_000), "1.5M")

    def test_thousands(self):
        self.assertEqual(_fmt_tokens(340_000), "340.0K")

    def test_small(self):
        self.assertEqual(_fmt_tokens(850), "850")

    def test_zero(self):
        self.assertEqual(_fmt_tokens(0), "0")

    def test_none(self):
        self.assertEqual(_fmt_tokens(None), "0")


class TestBar(unittest.TestCase):
    def test_bar_zero(self):
        out = bar(0, width=10, no_color=True)
        self.assertEqual(out, "░" * 10)

    def test_bar_full(self):
        out = bar(100, width=10, no_color=True)
        self.assertEqual(out, "█" * 10)

    def test_bar_half(self):
        out = bar(50, width=10, no_color=True)
        self.assertEqual(out.count("█"), 5)
        self.assertEqual(out.count("░"), 5)

    def test_bar_clamps_above_100(self):
        out = bar(250, width=10, no_color=True)
        self.assertEqual(out, "█" * 10)

    def test_bar_clamps_below_0(self):
        out = bar(-10, width=10, no_color=True)
        self.assertEqual(out, "░" * 10)

    def test_bar_color_emitted(self):
        out = bar(50, width=10, no_color=False)
        self.assertIn("\033[", out)


class TestConfig(unittest.TestCase):
    def test_deep_merge_keeps_defaults(self):
        merged = deep_merge(
            {"opencode": {"enabled": True, "days": [1, 7]}, "copilot_cli": {"enabled": True}},
            {"opencode": {"days": [3]}},
        )
        self.assertTrue(merged["opencode"]["enabled"])
        self.assertEqual(merged["opencode"]["days"], [3])
        self.assertTrue(merged["copilot_cli"]["enabled"])

    def test_configured_days_default(self):
        self.assertEqual(configured_days({}), [1, 7])

    def test_configured_days_custom(self):
        self.assertEqual(configured_days({"days": [3, 14]}), [3, 14])

    def test_configured_days_dedup(self):
        self.assertEqual(configured_days({"days": [1, 1, 7]}), [1, 7])

    def test_configured_days_invalid_entries(self):
        self.assertEqual(configured_days({"days": ["bad", 0, -1, 5]}), [5])


class TestDisplayConfig(unittest.TestCase):
    def test_load_display_config_default(self):
        cfg = load_display_config()
        self.assertTrue(cfg["auto_hide_enabled"])
        self.assertEqual(cfg["auto_hide_days"], 1)
        self.assertEqual(cfg["amp_usable_pct"], 30.0)


class TestGetToolIcon(unittest.TestCase):
    def test_known_tool(self):
        from limitlens.core import get_tool_icon
        self.assertEqual(get_tool_icon(tool_key="antigravity"), "🪐")
        self.assertEqual(get_tool_icon(name="antigrav"), "🪐")
        self.assertEqual(get_tool_icon(section="codex"), "⚡")

    def test_custom_keyword(self):
        from limitlens.core import get_tool_icon
        self.assertEqual(get_tool_icon(name="my-claude-tool"), "🧠")
        self.assertEqual(get_tool_icon(name="openai-gpt-4"), "🌀")

    def test_deterministic_fallback(self):
        import zlib
        from limitlens.core import get_tool_icon, _FALLBACK_POOL
        name = "custom-unmatched-tool"
        expected_idx = zlib.adler32(name.lower().encode('utf-8')) % len(_FALLBACK_POOL)
        expected_emoji = _FALLBACK_POOL[expected_idx]
        self.assertEqual(get_tool_icon(name=name), expected_emoji)
        # Verify it is consistent
        self.assertEqual(get_tool_icon(name=name), get_tool_icon(name=name))


if __name__ == "__main__":
    unittest.main()
