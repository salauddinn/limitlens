#!/usr/bin/env python3
"""Tests for limitlens.providers.antigravity — profile fetch, model filtering, cache."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from limitlens.providers import antigravity as ag_mod
from limitlens.providers.antigravity import (
    _fetch_single_profile,
    is_agy_cli_process_command,
)


class TestAntigravityStatus(unittest.TestCase):
    def test_is_agy_cli_process_command(self):
        self.assertTrue(is_agy_cli_process_command("agy"))
        self.assertTrue(is_agy_cli_process_command("/Users/me/.local/bin/agy --continue"))
        self.assertFalse(is_agy_cli_process_command("rg agy"))
        self.assertFalse(is_agy_cli_process_command("/bin/zsh -c agy"))

    def test_fetch_single_profile_deduplicates_same_quota_same_family(self):
        """Models in the same family with identical quota/reset are collapsed to the best one."""
        now = datetime.now(timezone.utc)
        reset_time = (now + timedelta(hours=2)).isoformat()
        models = [
            {
                "label": "Gemini Flash",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
            {
                "label": "Gemini Pro",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
            {
                "label": "Claude Sonnet",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
            {
                "label": "Claude Sonnet 4.6",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
            {
                "label": "GPT-OSS 120B",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
        ]

        with patch.object(ag_mod, "find_language_server_for_main_profile", return_value=("token", [12345], None, {})), \
             patch.object(ag_mod, "probe_ag_port", return_value=(12345, "token", None, False)), \
             patch.object(ag_mod, "get_ag_model_quotas", return_value=(models, None, False)):
            prof_data, should_cache = _fetch_single_profile(
                "ide",
                "Darwin",
                {"profiles": {}},
                is_main=True,
                known_profiles=[],
            )

        labels = [m["label"] for m in prof_data["models"]]
        self.assertTrue(should_cache)
        # Flash and Pro share the same quota+reset → collapsed to the best (Pro)
        self.assertNotIn("Gemini Flash", labels)
        self.assertIn("Gemini Pro", labels)
        # Claude Sonnet 4.6 is hidden, Claude Sonnet remains
        self.assertIn("Claude Sonnet", labels)
        self.assertNotIn("Claude Sonnet 4.6", labels)
        self.assertNotIn("GPT-OSS 120B", labels)

    def test_fetch_single_profile_keeps_flash_when_different_quota(self):
        """Flash with a different quota than Pro should appear as a separate entry."""
        now = datetime.now(timezone.utc)
        reset_time = (now + timedelta(hours=2)).isoformat()
        models = [
            {
                "label": "Gemini Flash",
                "pct_left": 50.0,
                "reset_time": reset_time,
            },
            {
                "label": "Gemini Pro",
                "pct_left": 95.0,
                "reset_time": reset_time,
            },
        ]

        with patch.object(ag_mod, "find_language_server_for_main_profile", return_value=("token", [12345], None, {})), \
             patch.object(ag_mod, "probe_ag_port", return_value=(12345, "token", None, False)), \
             patch.object(ag_mod, "get_ag_model_quotas", return_value=(models, None, False)):
            prof_data, should_cache = _fetch_single_profile(
                "ide",
                "Darwin",
                {"profiles": {}},
                is_main=True,
                known_profiles=[],
            )

        labels = [m["label"] for m in prof_data["models"]]
        self.assertTrue(should_cache)
        # Different quotas → both should appear
        self.assertIn("Gemini Flash", labels)
        self.assertIn("Gemini Pro", labels)

    def test_get_profile_name_from_config_dir(self):
        # Default dir
        default_dir = "/Users/testuser/.gemini/antigravity-cli"
        with patch("os.path.expanduser", return_value=default_dir):
            name = ag_mod.get_profile_name_from_config_dir(default_dir)
            self.assertEqual(name, "agy-cli")
            
            # Custom dir
            custom_dir = "/Users/testuser/agy-profile-work/.gemini/antigravity-cli"
            name = ag_mod.get_profile_name_from_config_dir(custom_dir)
            self.assertEqual(name, "agy-profile-work")

            # Custom dir nested differently
            custom_dir2 = "/Users/testuser/custom_agy/.gemini/antigravity-cli"
            name = ag_mod.get_profile_name_from_config_dir(custom_dir2)
            self.assertEqual(name, "custom_agy")

    @patch("subprocess.run")
    def test_get_config_dir_for_pid_macos(self, mock_run):
        import subprocess
        # Mock lsof output for mac
        mock_proc = unittest.mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "n/Users/testuser/agy-work-profile/.gemini/antigravity-cli/settings.json\n"
        mock_run.return_value = mock_proc

        config_dir = ag_mod.get_config_dir_for_pid(99999, "Darwin")
        self.assertEqual(config_dir, "/Users/testuser/agy-work-profile/.gemini/antigravity-cli")
        mock_run.assert_called_once_with(
            ["lsof", "-F", "n", "-p", "99999"],
            capture_output=True, text=True, timeout=5, errors="replace"
        )

    def test_get_config_dir_for_pid_linux_env(self):
        import builtins
        original_open = builtins.open
        
        def mock_open(file, *args, **kwargs):
            if "/proc/99999/environ" in str(file):
                m = unittest.mock.mock_open(read_data=b"PATH=/bin\x00HOME=/home/testuser/agy-work-profile\x00USER=test\x00")()
                return m
            return original_open(file, *args, **kwargs)

        with patch("builtins.open", mock_open):
            config_dir = ag_mod.get_config_dir_for_pid(99999, "Linux")
            self.assertEqual(config_dir, "/home/testuser/agy-work-profile/.gemini/antigravity-cli")

    @patch("subprocess.run")
    @patch("limitlens.providers.antigravity.get_config_dir_for_pid")
    @patch("limitlens.providers.antigravity.collect_listening_ports")
    def test_discover_active_cli_profiles(self, mock_collect_ports, mock_get_config, mock_run):
        mock_proc = unittest.mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "12345 1 agy\n23456 1 /usr/local/bin/agy --conversation=123\n"
        mock_run.return_value = mock_proc

        # Mock config dirs
        mock_get_config.side_effect = lambda pid, sys_name: (
            "/Users/testuser/agy-personal/.gemini/antigravity-cli" if str(pid) == "12345"
            else "/Users/testuser/agy-work/.gemini/antigravity-cli"
        )

        mock_collect_ports.side_effect = lambda pids, sys_name: (
            ([62125], None) if pids == ["12345"] else ([62126], None)
        )

        active = ag_mod.discover_active_cli_profiles("Darwin")
        self.assertIn("agy-personal", active)
        self.assertIn("agy-work", active)
        self.assertEqual(active["agy-personal"]["ports"], [62125])
        self.assertEqual(active["agy-work"]["ports"], [62126])

    @patch("limitlens.providers.antigravity.discover_active_cli_profiles")
    @patch("limitlens.providers.antigravity.get_antigravity_named_profiles")
    @patch("limitlens.providers.antigravity.load_antigravity_cache")
    @patch("limitlens.providers.antigravity._fetch_single_profile")
    @patch("limitlens.providers.antigravity.try_save_antigravity_cache")
    def test_get_antigravity_data_multiple_profiles(self, mock_save_cache, mock_fetch, mock_load_cache, mock_named_profs, mock_discover_cli):
        # 1 active CLI, 1 cached stale CLI, 1 IDE profile
        mock_named_profs.return_value = (["work-ide"], None)
        
        mock_discover_cli.return_value = {
            "agy-work": {
                "pid": "12345",
                "ports": [62125],
                "config_dir": "/Users/testuser/agy-work/.gemini/antigravity-cli"
            }
        }

        mock_load_cache.return_value = {
            "profiles": {
                "agy-personal": {
                    "models": [{"label": "Gemini Pro", "pct_left": 80.0}],
                    "fetched_at": "2026-05-20T12:00:00Z",
                    "config_dir": "/Users/testuser/agy-personal/.gemini/antigravity-cli"
                }
            }
        }

        # Mock fetch calls for all profiles
        def mock_fetch_impl(profile, sys_name, cache, is_main=False, known_profiles=None, source="ide", cli_info=None):
            if profile == "work-ide":
                return {"name": "work-ide", "status": "running", "models": [{"label": "Gemini Flash", "pct_left": 90.0}]}, True
            elif profile == "ide":
                return {"name": "ide", "status": "stopped", "error": "not running"}, False
            elif profile == "agy-work":
                return {"name": "agy-work", "status": "running", "models": [{"label": "Claude Sonnet", "pct_left": 75.0}], "home_dir": "~/agy-work"}, True
            elif profile == "agy-personal":
                # Stale cached fallback
                prof_data = {"name": "agy-personal", "status": "stopped", "error": "agy CLI not running"}
                ag_mod.apply_antigravity_cached_fallback(prof_data, cache, "agy CLI not running")
                return prof_data, False
            return {"name": profile, "status": "unknown"}, False

        mock_fetch.side_effect = mock_fetch_impl

        res = ag_mod.get_antigravity_data(unittest.mock.Mock(no_color=False, verbose=False))
        
        profiles = {p["name"]: p for p in res["profiles"]}
        self.assertIn("work-ide", profiles)
        self.assertIn("ide", profiles)
        self.assertIn("agy-work", profiles)
        self.assertIn("agy-personal", profiles)

        self.assertEqual(profiles["work-ide"]["status"], "running")
        self.assertEqual(profiles["agy-work"]["status"], "running")
        self.assertEqual(profiles["agy-personal"]["status"], "stale")
        self.assertEqual(profiles["agy-personal"]["models"][0]["label"], "Gemini Pro")


if __name__ == "__main__":
    unittest.main()

