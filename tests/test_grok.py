#!/usr/bin/env python3
"""Tests for limitlens.providers.grok."""

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from limitlens.providers.grok import (
    display_grok_text,
    get_grok_data,
    _load_auth,
    _login_status,
    TIER_LABELS,
)


def _args(**kwargs):
    defaults = {"no_color": True, "verbose": False, "all": False, "redact": False, "tool": "all"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


AUTH_LOGGED_IN = {
    "https://auth.x.ai::test-client": {
        "key": "header.eyJzdWIiOiJ1c2VyMTIzIiwidGVhbV9pZCI6InRlYW0tYWJjIn0.sig",
        "refresh_token": "refresh-secret",
        "auth_mode": "oidc",
        "create_time": "2026-07-09T11:00:00Z",
        "user_id": "user-123",
        "email": "test@example.com",
        "first_name": "Test",
        "team_id": "team-abc-123",
        "tier": 4,
        "expires_at": "2099-12-31T23:59:59.000000Z",
        "oidc_issuer": "https://auth.x.ai",
        "oidc_client_id": "test-client",
    }
}

AUTH_EXPIRED = {
    "https://auth.x.ai::test-client": {
        "key": "header.payload.sig",
        "refresh_token": "refresh-secret",
        "auth_mode": "oidc",
        "user_id": "user-123",
        "email": "test@example.com",
        "first_name": "Test",
        "team_id": "team-abc-123",
        "tier": 4,
        "expires_at": "2020-01-01T00:00:00.000000Z",
    }
}


class TestLoginStatus(unittest.TestCase):
    def test_logged_in(self):
        state, label = _login_status("2099-12-31T23:59:59Z")
        self.assertEqual(state, "logged_in")
        self.assertEqual(label, "logged in")

    def test_expired(self):
        state, label = _login_status("2020-01-01T00:00:00Z")
        self.assertEqual(state, "expired")
        self.assertIn("expired", label)

    def test_none(self):
        state, label = _login_status(None)
        self.assertEqual(state, "unknown")

    def test_invalid(self):
        state, label = _login_status("not-a-date")
        self.assertEqual(state, "unknown")


class TestLoadAuth(unittest.TestCase):
    def test_reads_safe_fields_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(AUTH_LOGGED_IN, f)
            path = f.name
        try:
            with patch("limitlens.providers.grok.GROK_AUTH_PATH", path):
                data = _load_auth()
            self.assertIsNotNone(data)
            # _load_auth() returns a list of account dicts
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 1)
            entry = data[0]
            self.assertEqual(entry["email"], "test@example.com")
            self.assertEqual(entry["team_id"], "team-abc-123")
            self.assertEqual(entry["tier"], 4)
            # Must NOT include token fields
            self.assertNotIn("key", entry)
            self.assertNotIn("refresh_token", entry)
        finally:
            os.unlink(path)

    def test_missing_file_returns_none(self):
        with patch("limitlens.providers.grok.GROK_AUTH_PATH", "/nonexistent/path/auth.json"):
            self.assertIsNone(_load_auth())

    def test_invalid_json_returns_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("not json {")
            path = f.name
        try:
            with patch("limitlens.providers.grok.GROK_AUTH_PATH", path):
                self.assertIsNone(_load_auth())
        finally:
            os.unlink(path)

    def test_empty_dict_returns_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({}, f)
            path = f.name
        try:
            with patch("limitlens.providers.grok.GROK_AUTH_PATH", path):
                self.assertIsNone(_load_auth())
        finally:
            os.unlink(path)


class TestGetGrokData(unittest.TestCase):
    def _write_auth(self, payload):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, f)
        f.close()
        return f.name

    def test_not_installed_when_grok_dir_missing(self):
        with patch("limitlens.providers.grok._safe_exists", return_value=False):
            data = get_grok_data(_args())
        self.assertEqual(data["status"], "not_installed")
        self.assertFalse(data["installed"])

    def test_logged_in(self):
        auth_file = self._write_auth(AUTH_LOGGED_IN)
        try:
            with (
                patch("limitlens.providers.grok._safe_exists", return_value=True),
                patch("limitlens.providers.grok.GROK_AUTH_PATH", auth_file),
                patch("limitlens.providers.grok._load_default_model", return_value="grok-4.5"),
            ):
                data = get_grok_data(_args())
        finally:
            os.unlink(auth_file)

        self.assertEqual(data["status"], "logged_in")
        self.assertTrue(data["installed"])
        # accounts are nested under data["accounts"]
        account = data["accounts"][0]
        self.assertEqual(account["email"], "test@example.com")
        self.assertEqual(account["team_id"], "team-abc-123")
        self.assertEqual(account["tier"], 4)
        self.assertEqual(account["tier_label"], "Pro")
        self.assertEqual(data["default_model"], "grok-4.5")

    def test_redacts_email(self):
        auth_file = self._write_auth(AUTH_LOGGED_IN)
        try:
            with (
                patch("limitlens.providers.grok._safe_exists", return_value=True),
                patch("limitlens.providers.grok.GROK_AUTH_PATH", auth_file),
                patch("limitlens.providers.grok._load_default_model", return_value=None),
            ):
                data = get_grok_data(_args(redact=True))
        finally:
            os.unlink(auth_file)
        # redact=True should mask the email in the accounts list
        account = data["accounts"][0]
        self.assertNotEqual(account["email"], "test@example.com")
        self.assertIn("***", account["email"])

    def test_expired_session(self):
        auth_file = self._write_auth(AUTH_EXPIRED)
        try:
            with (
                patch("limitlens.providers.grok._safe_exists", return_value=True),
                patch("limitlens.providers.grok.GROK_AUTH_PATH", auth_file),
                patch("limitlens.providers.grok._load_default_model", return_value=None),
            ):
                data = get_grok_data(_args())
        finally:
            os.unlink(auth_file)
        self.assertEqual(data["status"], "logged_in")
        # account status inside accounts list should be expired
        self.assertEqual(data["accounts"][0]["status"], "expired")

    def test_no_auth_file(self):
        with (
            patch("limitlens.providers.grok._safe_exists", return_value=True),
            patch("limitlens.providers.grok.GROK_AUTH_PATH", "/nonexistent/auth.json"),
            patch("limitlens.providers.grok._load_default_model", return_value=None),
        ):
            data = get_grok_data(_args())
        self.assertEqual(data["status"], "not_logged_in")


class TestDisplayGrokText(unittest.TestCase):
    def _capture(self, data, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            display_grok_text(data, _args(**kw))
        return buf.getvalue()

    def test_none_returns_nothing(self):
        self.assertEqual(display_grok_text(None, _args()), None)

    def test_logged_in_shows_section(self):
        data = {
            "name": "Grok", "command": "grok", "installed": True,
            "status": "logged_in",
            "default_model": "grok-4.5",
            "accounts": [
                {
                    "email": "te***@example.com",
                    "tier_label": "Pro",
                    "auth_mode": "oidc",
                    "status": "logged_in",
                    "login_label": "logged in",
                }
            ],
            "windows": [],
        }
        out = self._capture(data, tool="grok")
        self.assertIn("Grok", out)
        # login_label is in accounts; display_grok_text shows it
        self.assertIn("logged", out.lower())
        self.assertIn("Pro", out)
        self.assertIn("grok-4.5", out)

    def test_not_installed_hidden_by_default(self):
        data = {
            "name": "Grok", "command": "grok", "installed": False,
            "status": "not_installed", "note": "Install from grok.com",
        }
        # Default view (tool=all, not verbose) should hide it
        out = self._capture(data, tool="all", verbose=False)
        self.assertEqual(out, "")

    def test_not_installed_shown_with_verbose(self):
        data = {
            "name": "Grok", "command": "grok", "installed": False,
            "status": "not_installed", "note": "Install from grok.com",
        }
        out = self._capture(data, verbose=True)
        self.assertIn("not installed", out)

    def test_expired_shown_with_verbose(self):
        data = {
            "name": "Grok", "command": "grok", "installed": True,
            "status": "expired", "login_label": "session expired (run `grok login`)",
            "email": "te***@example.com",
        }
        out = self._capture(data, verbose=True)
        self.assertIn("expired", out)

    def test_explicit_tool_shows_not_logged_in(self):
        data = {
            "name": "Grok", "command": "grok", "installed": True,
            "status": "not_logged_in", "note": "run `grok login`",
            "default_model": None,
        }
        out = self._capture(data, tool="grok")
        self.assertIn("not signed in", out)

    def test_tier_labels(self):
        self.assertEqual(TIER_LABELS.get(4), "Pro")
        self.assertEqual(TIER_LABELS.get(0), "Free")


if __name__ == "__main__":
    unittest.main()
