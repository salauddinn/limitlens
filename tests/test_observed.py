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

    def test_credit_limits_from_config(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE message (time_created integer NOT NULL, data text NOT NULL)")
            conn.commit()
            conn.close()

            result = get_opencode_usage({
                "opencode": {
                    "enabled": True,
                    "db_path": db_path,
                    "days": [1],
                    "credit_limits": [
                        {"name": "monthly", "total": 100, "remaining": 60},
                        {"name": "api", "remaining": 7, "used": 3},
                    ],
                }
            })

            limits = {limit["name"]: limit for limit in result["credit_limits"]}
            self.assertEqual(limits["monthly"]["used"], 40.0)
            self.assertEqual(limits["monthly"]["pct_left"], 60.0)
            self.assertEqual(limits["api"]["total"], 10.0)
        finally:
            os.unlink(db_path)

    def test_ignored_models_exclude_duplicate_usage(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE message (time_created integer NOT NULL, data text NOT NULL)")
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            for provider, model in (("openai", "gpt-5.5"), ("anthropic", "claude-test")):
                payload = {
                    "role": "assistant",
                    "providerID": provider,
                    "modelID": model,
                    "tokens": {"total": 100},
                    "time": {"created": now_ms},
                }
                conn.execute("INSERT INTO message VALUES (?, ?)", (now_ms, json.dumps(payload)))
            conn.commit()
            conn.close()

            result = get_opencode_usage({
                "opencode": {
                    "enabled": True,
                    "db_path": db_path,
                    "days": [1],
                    "ignored_models": ["openai/gpt-5.5"],
                }
            })

            models = result["windows"][0]["models"]
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["provider"], "anthropic")
            self.assertEqual(models[0]["model"], "claude-test")
        finally:
            os.unlink(db_path)

    def test_model_parent_labels_are_added(self):
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
                "tokens": {"total": 100},
                "time": {"created": now_ms},
            }
            conn.execute("INSERT INTO message VALUES (?, ?)", (now_ms, json.dumps(payload)))
            conn.commit()
            conn.close()

            result = get_opencode_usage({
                "opencode": {
                    "enabled": True,
                    "db_path": db_path,
                    "days": [1],
                    "model_parents": {"google-vertex/*": "Vertex Free Trial"},
                }
            })

            model = result["windows"][0]["models"][0]
            self.assertEqual(model["parent"], "Vertex Free Trial")
        finally:
            os.unlink(db_path)

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

    def test_display_opencode_credit_limits(self):
        data = {
            "opencode": {
                "credit_limits": [
                    {
                        "name": "monthly",
                        "unit": "credits",
                        "total": 100.0,
                        "remaining": 60.0,
                        "used": 40.0,
                        "pct_left": 60.0,
                    }
                ],
                "windows": [],
            },
            "copilot_cli": {"disabled": True},
        }
        args = argparse.Namespace(tool="opencode", verbose=False, all=False, no_color=True)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_opencode_text(data, args)

        output = buf.getvalue()
        self.assertIn("monthly", output)
        self.assertIn("60.0% left", output)
        self.assertIn("used 40.00 credits", output)

    def test_display_parent_and_inr_credit_limits(self):
        data = {
            "opencode": {
                "credit_limits": [
                    {
                        "name": "Vertex Free Trial",
                        "unit": "₹",
                        "total": 28442.99,
                        "remaining": 27793.82,
                        "used": 649.17,
                        "pct_left": 97.72,
                    }
                ],
                "windows": [
                    {
                        "days": 1,
                        "models": [
                            {
                                "provider": "google-vertex",
                                "model": "gemini-test",
                                "requests": 1,
                                "cost": 0,
                                "tokens": {"total": 100},
                                "parent": "Vertex Free Trial",
                            }
                        ],
                    }
                ],
            },
            "copilot_cli": {"disabled": True},
        }
        args = argparse.Namespace(tool="opencode", verbose=False, all=False, no_color=True)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_opencode_text(data, args)

        output = buf.getvalue()
        self.assertIn("₹27793.82/₹28442.99", output)
        self.assertIn("parent: Vertex Free Trial", output)


if __name__ == "__main__":
    unittest.main()
