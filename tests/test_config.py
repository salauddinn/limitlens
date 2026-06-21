#!/usr/bin/env python3
"""
Comprehensive tests for limitlens.config.

Covers deep_merge, configured_days, load_display_config,
validate_config_types, apply_env_overrides, load_limitlens_config,
is_provider_enabled, reset_custom_tool_spend, and ConfigValidationError.
"""
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from limitlens.config import (
    ConfigValidationError,
    DEFAULT_CONFIG,
    apply_env_overrides,
    atomic_write_json,
    configured_days,
    deep_merge,
    is_provider_enabled,
    load_display_config,
    load_limitlens_config,
    reset_custom_tool_spend,
    validate_config_types,
)


# ── Original tests (preserved) ──────────────────────────────────────────────

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

    @patch("limitlens.config.os.path.exists", return_value=False)
    def test_load_display_config_new_keys(self, mock_exists):
        """New keys added in improvements must have correct defaults."""
        cfg = load_display_config()
        self.assertEqual(cfg["menubar_refresh_seconds"], 300)
        self.assertEqual(cfg["notify_warn_pct"], 30.0)
        self.assertEqual(cfg["notify_critical_pct"], 10.0)


# ── is_provider_enabled ──────────────────────────────────────────────────────

class TestIsProviderEnabled(unittest.TestCase):
    def test_default_true_when_key_missing(self):
        self.assertTrue(is_provider_enabled({}, "codex", default=True))

    def test_default_false_when_key_missing(self):
        self.assertFalse(is_provider_enabled({}, "pi", default=False))

    def test_bool_true(self):
        self.assertTrue(is_provider_enabled({"amp": {"enabled": True}}, "amp"))

    def test_bool_false(self):
        self.assertFalse(is_provider_enabled({"amp": {"enabled": False}}, "amp"))

    def test_string_false(self):
        self.assertFalse(is_provider_enabled({"codex": {"enabled": "false"}}, "codex"))

    def test_string_zero(self):
        self.assertFalse(is_provider_enabled({"codex": {"enabled": "0"}}, "codex"))

    def test_string_no(self):
        self.assertFalse(is_provider_enabled({"codex": {"enabled": "no"}}, "codex"))

    def test_string_true_is_enabled(self):
        self.assertTrue(is_provider_enabled({"codex": {"enabled": "true"}}, "codex"))

    def test_non_dict_section_falls_back_to_default(self):
        self.assertTrue(is_provider_enabled({"codex": "yes"}, "codex", default=True))
        self.assertFalse(is_provider_enabled({"codex": "yes"}, "codex", default=False))

    def test_missing_enabled_key_uses_default(self):
        self.assertTrue(is_provider_enabled({"codex": {}}, "codex", default=True))
        self.assertFalse(is_provider_enabled({"codex": {}}, "codex", default=False))


# ── validate_config_types ────────────────────────────────────────────────────

class TestValidateConfigTypes(unittest.TestCase):
    def test_valid_bool_passes(self):
        validate_config_types({"cursor": {"enabled": True}})

    def test_unknown_top_level_key_raises(self):
        with self.assertRaises(ConfigValidationError):
            validate_config_types({"nonexistent_provider": {}})

    def test_wrong_type_bool_raises(self):
        with self.assertRaises(ConfigValidationError):
            validate_config_types({"cursor": {"enabled": "yes"}})  # must be bool

    def test_wrong_type_string_raises(self):
        with self.assertRaises(ConfigValidationError):
            validate_config_types({"commandcode": {"credits_url": 123}})

    def test_wrong_type_float_raises(self):
        with self.assertRaises(ConfigValidationError):
            validate_config_types({"display": {"amp_usable_pct": "high"}})

    def test_nested_unknown_key_raises(self):
        with self.assertRaises(ConfigValidationError):
            validate_config_types({"cursor": {"no_such_key": True}})


# ── apply_env_overrides ──────────────────────────────────────────────────────

class TestApplyEnvOverrides(unittest.TestCase):
    def test_bool_false_override(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_CURSOR_ENABLED": "false"}):
            apply_env_overrides(config)
        self.assertFalse(config["cursor"]["enabled"])

    def test_bool_true_override(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_PI_ENABLED": "true"}):
            apply_env_overrides(config)
        self.assertTrue(config["pi"]["enabled"])

    def test_float_override(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_DISPLAY_AMP_USABLE_PCT": "50.0"}):
            apply_env_overrides(config)
        self.assertAlmostEqual(config["display"]["amp_usable_pct"], 50.0)

    def test_invalid_float_raises(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_DISPLAY_AMP_USABLE_PCT": "not_a_float"}):
            with self.assertRaises(ConfigValidationError):
                apply_env_overrides(config)

    def test_list_override(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_OPENCODE_DAYS": "3,14"}):
            apply_env_overrides(config)
        self.assertEqual(config["opencode"]["days"], ["3", "14"])

    def test_string_override(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        with patch.dict(os.environ, {"LIMITLENS_AGENTROUTER_QUOTA_URL": "https://example.com/quota"}):
            apply_env_overrides(config)
        self.assertEqual(config["agentrouter"]["quota_url"], "https://example.com/quota")


# ── load_limitlens_config ─────────────────────────────────────────────────────

class TestLoadLimitlensConfig(unittest.TestCase):
    def test_loads_valid_json_and_merges(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"cursor": {"enabled": False}}, f)
            path = f.name
        try:
            with patch.dict(os.environ, {"LIMITLENS_CONFIG": path}):
                config = load_limitlens_config()
            self.assertFalse(config["cursor"]["enabled"])
            # Defaults for other providers preserved
            self.assertTrue(config["codex"]["enabled"])
        finally:
            os.unlink(path)

    def test_invalid_json_raises_config_validation_error(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("this is definitely not json {{{")
            path = f.name
        try:
            with patch.dict(os.environ, {"LIMITLENS_CONFIG": path}):
                with self.assertRaises(ConfigValidationError):
                    load_limitlens_config()
        finally:
            os.unlink(path)

    @patch("limitlens.config.auto_detect_providers", return_value={})
    def test_missing_file_returns_full_defaults(self, mock_auto):
        with patch.dict(os.environ, {"LIMITLENS_CONFIG": "/nonexistent/path/config.json"}):
            config = load_limitlens_config()
        self.assertIn("codex", config)
        self.assertTrue(config["codex"]["enabled"])
        self.assertIn("display", config)

    def test_non_dict_json_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([1, 2, 3], f)
            path = f.name
        try:
            with patch.dict(os.environ, {"LIMITLENS_CONFIG": path}):
                with self.assertRaises(ConfigValidationError):
                    load_limitlens_config()
        finally:
            os.unlink(path)


# ── deep_merge ───────────────────────────────────────────────────────────────

class TestDeepMerge(unittest.TestCase):
    def test_nested_override_preserves_sibling(self):
        merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 99}})
        self.assertEqual(merged["a"]["x"], 1)
        self.assertEqual(merged["a"]["y"], 99)

    def test_scalar_override(self):
        merged = deep_merge({"a": 1}, {"a": 2})
        self.assertEqual(merged["a"], 2)

    def test_new_key_added(self):
        merged = deep_merge({"a": 1}, {"b": 2})
        self.assertIn("b", merged)
        self.assertEqual(merged["a"], 1)

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        deep_merge(base, {"a": {"x": 99}})
        self.assertEqual(base["a"]["x"], 1)

    def test_override_none_does_not_crash(self):
        merged = deep_merge({"a": 1}, None)
        self.assertEqual(merged["a"], 1)


# ── reset_custom_tool_spend ───────────────────────────────────────────────────

class TestResetCustomToolSpend(unittest.TestCase):
    def _write_config(self, data):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(data, f)
        f.close()
        return f.name

    def test_returns_false_when_file_missing(self):
        self.assertFalse(reset_custom_tool_spend("/nonexistent/config.json"))

    def test_returns_false_when_no_custom_tools(self):
        path = self._write_config({"cursor": {"enabled": True}})
        try:
            self.assertFalse(reset_custom_tool_spend(path))
        finally:
            os.unlink(path)

    def test_zeros_used_and_request_count(self):
        cfg = {
            "custom_tools": {
                "enabled": True,
                "tools": {
                    "kilo": {"name": "Kilo Code", "total": 1000, "used": 500, "request_count": 12}
                },
            }
        }
        path = self._write_config(cfg)
        try:
            result = reset_custom_tool_spend(path)
            self.assertTrue(result)
            with open(path) as f:
                updated = json.load(f)
            tool = updated["custom_tools"]["tools"]["kilo"]
            self.assertEqual(tool["used"], 0)
            self.assertEqual(tool["request_count"], 0)
            self.assertEqual(tool["total"], 1000)  # unchanged
        finally:
            os.unlink(path)

    def test_reset_custom_tool_spend_creates_backup_and_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "amp": {"enabled": False, "individual_credits": True},
                    "custom_tools": {
                        "enabled": True,
                        "tools": {
                            "demo": {"name": "Demo", "used": 12, "request_count": 3, "total": 100}
                        },
                    },
                }, f)

            self.assertTrue(reset_custom_tool_spend(path))

            with open(path, encoding="utf-8") as f:
                updated = json.load(f)
            self.assertEqual(updated["amp"], {"enabled": False, "individual_credits": True})
            self.assertEqual(updated["custom_tools"]["tools"]["demo"]["used"], 0)
            self.assertEqual(updated["custom_tools"]["tools"]["demo"]["request_count"], 0)

            backups = [name for name in os.listdir(temp_dir) if name.startswith("config.backup.")]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(temp_dir, backups[0]), encoding="utf-8") as f:
                backup = json.load(f)
            self.assertEqual(backup["custom_tools"]["tools"]["demo"]["used"], 12)

    def test_atomic_write_json_preserves_original_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"amp": {"enabled": False}}, f)

            with patch("limitlens.config.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"amp": {"enabled": True}})

            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"amp": {"enabled": False}})
            leftovers = [name for name in os.listdir(temp_dir) if name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_atomic_write_json_preserves_config_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not supported on this platform")
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = os.path.join(temp_dir, "dotfiles-limitlens.json")
            link_path = os.path.join(temp_dir, "config.json")
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump({"amp": {"enabled": False}}, f)
            os.symlink(target_path, link_path)

            atomic_write_json(link_path, {"amp": {"enabled": True}})

            self.assertTrue(os.path.islink(link_path))
            with open(target_path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"amp": {"enabled": True}})

    def test_returns_false_when_already_zeroed(self):
        cfg = {
            "custom_tools": {
                "enabled": True,
                "tools": {"kilo": {"used": 0, "request_count": 0}},
            }
        }
        path = self._write_config(cfg)
        try:
            self.assertFalse(reset_custom_tool_spend(path))
        finally:
            os.unlink(path)

    def test_raises_on_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json")
            path = f.name
        try:
            with self.assertRaises(ConfigValidationError):
                reset_custom_tool_spend(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
