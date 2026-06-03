#!/usr/bin/env python3
import unittest
import os
from unittest.mock import patch
from limitlens.config import (
    deep_merge,
    configured_days,
    load_display_config,
    load_limitlens_config,
    ConfigValidationError
)

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
    @patch("limitlens.config.os.path.exists", return_value=False)
    def test_load_display_config_default(self, mock_exists):
        cfg = load_display_config()
        self.assertTrue(cfg["auto_hide_enabled"])
        self.assertEqual(cfg["auto_hide_days"], 1)
        self.assertEqual(cfg["amp_usable_pct"], 30.0)


if __name__ == "__main__":
    unittest.main()
