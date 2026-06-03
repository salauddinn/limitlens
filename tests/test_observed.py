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

from limitlens.providers.observed import get_opencode_usage, get_pi_usage, display_opencode_text


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

    def test_get_pi_usage_from_local_sessions(self):
        with tempfile.TemporaryDirectory() as sessions_dir:
            session_path = os.path.join(sessions_dir, "project", "session.jsonl")
            os.makedirs(os.path.dirname(session_path))
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            records = [
                {"type": "session", "version": 3, "id": "s1", "timestamp": datetime.now(timezone.utc).isoformat()},
                {
                    "type": "message",
                    "id": "a1",
                    "parentId": None,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": {
                        "role": "assistant",
                        "provider": "openai-codex",
                        "model": "gpt-5.5",
                        "timestamp": now_ms,
                        "usage": {
                            "input": 100,
                            "output": 50,
                            "cacheRead": 25,
                            "cacheWrite": 5,
                            "totalTokens": 180,
                            "cost": {"total": 0.12},
                        },
                    },
                },
            ]
            with open(session_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")

            result = get_pi_usage({"pi": {"enabled": True, "sessions_dir": sessions_dir, "days": [1]}})

            models = result["windows"][0]["models"]
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["provider"], "openai-codex")
            self.assertEqual(models[0]["model"], "gpt-5.5")
            self.assertEqual(models[0]["requests"], 1)
            self.assertEqual(models[0]["tokens"]["total"], 180)
            self.assertEqual(models[0]["tokens"]["cache_read"], 25)
            self.assertAlmostEqual(models[0]["cost"], 0.12)

    def test_pi_ignored_models_exclude_duplicate_usage(self):
        with tempfile.TemporaryDirectory() as sessions_dir:
            session_path = os.path.join(sessions_dir, "session.jsonl")
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            with open(session_path, "w", encoding="utf-8") as f:
                for provider, model in (("openai-codex", "gpt-5.5"), ("google-vertex", "gemini-test")):
                    f.write(json.dumps({
                        "type": "message",
                        "id": model,
                        "parentId": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": {
                            "role": "assistant",
                            "provider": provider,
                            "model": model,
                            "timestamp": now_ms,
                            "usage": {"totalTokens": 100},
                        },
                    }) + "\n")

            result = get_pi_usage({
                "pi": {
                    "enabled": True,
                    "sessions_dir": sessions_dir,
                    "days": [1],
                    "ignored_models": ["openai-codex/gpt-5.5"],
                }
            })

            models = result["windows"][0]["models"]
            self.assertEqual(len(models), 1)
            self.assertEqual(models[0]["provider"], "google-vertex")

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

    def test_display_pi_error_for_direct_tool(self):
        data = {
            "opencode": {"windows": []},
            "pi": {"error": "Pi sessions dir not found: ~/.pi/agent/sessions"},
            "copilot_cli": {"disabled": True},
        }
        args = argparse.Namespace(tool="pi", verbose=False, all=False, no_color=True)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_opencode_text(data, args)

        output = buf.getvalue()
        self.assertIn("pi", output)
        self.assertIn("Pi sessions dir not found", output)

    def test_display_pi_usage(self):
        data = {
            "opencode": {"windows": []},
            "pi": {
                "windows": [
                    {
                        "days": 1,
                        "models": [
                            {
                                "provider": "openai-codex",
                                "model": "gpt-5.5",
                                "requests": 2,
                                "cost": 0.5,
                                "tokens": {"total": 1234},
                            }
                        ],
                    }
                ]
            },
            "copilot_cli": {"disabled": True},
        }
        args = argparse.Namespace(tool="opencode", verbose=False, all=False, no_color=True)
        buf = io.StringIO()

        with redirect_stdout(buf):
            display_opencode_text(data, args)

        output = buf.getvalue()
        self.assertIn("pi", output)
        self.assertIn("gpt-5.5", output)
        self.assertIn("1.2K tokens", output)

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
