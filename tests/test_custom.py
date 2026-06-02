#!/usr/bin/env python3
"""Tests for limitlens.providers.custom — config-only custom quota tools."""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from limitlens.providers.custom import display_custom_text, get_custom_data


class TestCustomProvider(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(no_color=True, verbose=False, all=False)

    @patch("limitlens.providers.custom.load_display_config", return_value={"auto_hide_enabled": False})
    def test_get_custom_data_from_config(self, mock_display):
        data = get_custom_data(self.args, {
            "custom_tools": {
                "tools": {
                    "my-tool": {
                        "name": "My Tool",
                        "total": 100,
                        "remaining": 75,
                        "unit": "credits",
                        "request_count": 12,
                    }
                }
            }
        })

        tool = data["tools"][0]
        tier = tool["tiers"][0]
        self.assertEqual(tool["name"], "My Tool")
        self.assertEqual(tier["remaining"], 75.0)
        self.assertEqual(tier["used"], 25.0)
        self.assertEqual(tier["pct_left"], 75.0)

    @patch("limitlens.providers.custom.load_display_config", return_value={"auto_hide_enabled": False})
    def test_disabled_custom_tool_is_skipped(self, mock_display):
        data = get_custom_data(self.args, {
            "custom_tools": {
                "tools": {
                    "off": {"enabled": False, "total": 100, "remaining": 75}
                }
            }
        })

        self.assertEqual(data["tools"], [])

    @patch("limitlens.providers.custom.load_display_config", return_value={"auto_hide_enabled": False})
    def test_display_custom_tool(self, mock_display):
        data = get_custom_data(self.args, {
            "custom_tools": {
                "tools": {
                    "my-tool": {
                        "name": "My Tool",
                        "total": 100,
                        "remaining": 75,
                        "unit": "credits",
                        "request_count": 12,
                        "note": "manual source",
                    }
                }
            }
        })
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_custom_text(data, self.args)

        output = buf.getvalue()
        self.assertIn("Custom Tools", output)
        self.assertIn("My Tool", output)
        self.assertIn("75.0% left", output)
        self.assertIn("manual source", output)


if __name__ == "__main__":
    unittest.main()
