#!/usr/bin/env python3
"""Tests for limitlens.providers.observed — OpenCode SQLite + Copilot OTel usage."""

import json
import os
import sqlite3
import tempfile
import unittest
import argparse
import io
from contextlib import redirect_stdout
from datetime import datetime, timezone

from limitlens.providers.observed import get_opencode_usage, display_opencode_text


class TestOpenCodeUsage(unittest.TestCase):
    def test_get_opencode_usage_from_sqlite(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE message (time_created integer NOT NULL, data text NOT NULL)")
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            payload = {
                "role": "assistant",
                "providerID": "google-vertex",
                "modelID": "gemini-test",
                "cost": 0.25,
                "tokens": {
                    "total": 100,
                    "input": 30,
                    "output": 20,
                    "reasoning": 5,
                    "cache": {"read": 40, "write": 5},
                },
                "time": {"created": now_ms, "completed": now_ms + 1},
            }
            conn.execute("INSERT INTO message VALUES (?, ?)", (now_ms, json.dumps(payload)))
            conn.commit()
            conn.close()

            result = get_opencode_usage({
                "opencode": {
                    "enabled": True,
                    "db_path": db_path,
                    "days": [1],
                    "providers": [],
                }
            })

            models = result["windows"][0]["models"]
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["provider"], "google-vertex")
            self.assertEqual(models[0]["model"], "gemini-test")
            self.assertEqual(models[0]["requests"], 1)
            self.assertEqual(models[0]["tokens"]["total"], 100)
            self.assertEqual(models[0]["tokens"]["cache_read"], 40)
        finally:
            os.unlink(db_path)

    def test_disabled(self):
        result = get_opencode_usage({"opencode": {"enabled": False}})
        self.assertTrue(result.get("disabled"))

    def test_missing_db(self):
        result = get_opencode_usage({
            "opencode": {
                "enabled": True,
                "db_path": "/nonexistent/path/opencode.db",
                "days": [1],
                "providers": [],
            }
        })
        self.assertIn("error", result)

    def test_display_opencode_in_all_view_when_data_exists(self):
        data = {
            "opencode": {
                "windows": [
                    {
                        "days": 1,
                        "models": [
                            {
                                "provider": "openai",
                                "model": "gpt-test",
                                "requests": 1,
                                "cost": 0,
                                "tokens": {"total": 123},
                            }
                        ],
                    }
                ]
            },
            "copilot_cli": {"disabled": True},
        }
        args = argparse.Namespace(tool="all", verbose=False, all=False, no_color=True)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_opencode_text(data, args)

        self.assertIn("Spend / Usage", buf.getvalue())
        self.assertIn("gpt-test", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
