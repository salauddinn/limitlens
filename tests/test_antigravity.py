#!/usr/bin/env python3
"""Tests for limitlens.providers.antigravity — profile fetch, model filtering, cache."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, mock_open
import socket
import ssl
import urllib.error
import urllib.request

from limitlens.providers import antigravity as ag_mod
from limitlens.providers.antigravity import (
    _fetch_single_profile,
    is_agy_cli_process_command,
    profile_is_ignored,
    filter_antigravity_profiles,
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
            self.assertEqual(config_dir.replace("\\", "/"), "/home/testuser/agy-work-profile/.gemini/antigravity-cli")

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

    @patch("limitlens.providers.antigravity.platform.system", return_value="Darwin")
    @patch("limitlens.providers.antigravity.discover_active_cli_profiles")
    @patch("limitlens.providers.antigravity.get_antigravity_named_profiles")
    @patch("limitlens.providers.antigravity.load_antigravity_cache")
    @patch("limitlens.providers.antigravity._fetch_single_profile")
    @patch("limitlens.providers.antigravity.try_save_antigravity_cache")
    def test_get_antigravity_data_multiple_profiles(self, mock_save_cache, mock_fetch, mock_load_cache, mock_named_profs, mock_discover_cli, mock_platform):
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






class TestMoreAntigravity(unittest.TestCase):
    def test_is_agy_cli_process_command_empty(self):
        self.assertFalse(ag_mod.is_agy_cli_process_command(""))
        self.assertFalse(ag_mod.is_agy_cli_process_command(None))

    @patch("glob.glob")
    @patch("os.path.isdir")
    @patch("subprocess.run")
    def test_get_antigravity_named_profiles(self, mock_run, mock_isdir, mock_glob):
        mock_glob.return_value = ["/path/prof1", "/path/__default__profile__"]
        mock_isdir.return_value = True

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "123 agy\n456 /path/AntigravityProfiles/prof2/something\n"
        mock_run.return_value = mock_proc

        profs, err = ag_mod.get_antigravity_named_profiles("Darwin")
        self.assertEqual(err, None)
        self.assertEqual(profs, ["prof1", "prof2"])

        mock_run.side_effect = OSError("boom")
        profs, err = ag_mod.get_antigravity_named_profiles("Darwin")
        self.assertIn("boom", err)

    @patch("subprocess.run")
    def test_collect_listening_ports_linux(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:((\"node\",pid=123,fd=18))\n"
        mock_run.return_value = mock_proc

        ports, err = ag_mod.collect_listening_ports(["123"], "Linux")
        self.assertEqual(ports, [8080])
        self.assertIsNone(err)

        mock_proc.returncode = 1
        mock_proc.stderr = "error"
        ports, err = ag_mod.collect_listening_ports(["123"], "Linux")
        self.assertEqual(ports, [])
        self.assertIn("ss failed", err)

    @patch("subprocess.run")
    def test_collect_listening_ports_mac(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "node 123 user 18u IPv4 0x... 0t0 TCP *:8080 (LISTEN)\n"
        mock_run.return_value = mock_proc

        ports, err = ag_mod.collect_listening_ports(["123"], "Darwin")
        self.assertEqual(ports, [8080])
        self.assertIsNone(err)

    def test_extract_server_ports_from_command(self):
        self.assertEqual(ag_mod.extract_server_ports_from_command("foo --https_server_port 1234"), {1234})
        self.assertEqual(ag_mod.extract_server_ports_from_command("foo --https_server_port=1234"), {1234})
        self.assertEqual(ag_mod.extract_server_ports_from_command("foo"), set())

    def test_extract_https_port_token(self):
        port, token = ag_mod.extract_https_port_token("foo --https_server_port 1234 --csrf_token abc")
        self.assertEqual(port, 1234)
        self.assertEqual(token, "abc")
        self.assertEqual(ag_mod.extract_https_port_token("foo"), (None, None))

    @patch("subprocess.run")
    def test_find_language_server_for_profile(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100 Electron AntigravityProfiles/myprof/\n101 language_server_macos AntigravityProfiles/myprof/ --https_server_port 1234 --csrf_token abc\n"
        mock_run.return_value = mock_proc

        token, ports, err, tokens = ag_mod.find_language_server_for_profile("myprof", "Darwin")
        self.assertEqual(token, "abc")
        self.assertEqual(ports, [1234])
        self.assertEqual(tokens, {1234: "abc"})
        self.assertIsNone(err)

    @patch("subprocess.run")
    def test_find_language_server_for_main_profile(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100 1 Electron\n101 100 language_server_macos /extensions/antigravity/bin/ --https_server_port 1234 --csrf_token abc\n"
        mock_run.return_value = mock_proc

        token, ports, err, tokens = ag_mod.find_language_server_for_main_profile("Darwin", [])
        self.assertEqual(token, "abc")
        self.assertEqual(ports, [1234])
        self.assertEqual(tokens, {1234: "abc"})

    @patch("subprocess.run")
    def test_find_language_server_for_cli(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100 agy\n"
        mock_run.return_value = mock_proc

        with patch("limitlens.providers.antigravity.collect_listening_ports", return_value=([1234], None)):
            token, ports, err, tokens = ag_mod.find_language_server_for_cli("Darwin")
            self.assertEqual(ports, [1234])

    def test_format_ag_error(self):
        self.assertEqual(ag_mod.format_ag_error(TimeoutError()), "request timed out")
        self.assertEqual(ag_mod.format_ag_error(socket.timeout()), "request timed out")
        self.assertEqual(ag_mod.format_ag_error(ssl.SSLCertVerificationError()), "TLS certificate verification failed for local Antigravity endpoint")

        err = urllib.error.URLError(ssl.SSLCertVerificationError())
        self.assertEqual(ag_mod.format_ag_error(err), "TLS certificate verification failed for local Antigravity endpoint")

    def test_is_tls_verification_error(self):
        self.assertTrue(ag_mod.is_tls_verification_error(ssl.SSLCertVerificationError()))
        self.assertFalse(ag_mod.is_tls_verification_error(TimeoutError()))
        err = urllib.error.URLError(ssl.SSLCertVerificationError())
        self.assertTrue(ag_mod.is_tls_verification_error(err))

    @patch("urllib.request.urlopen")
    def test_make_ag_request(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = ag_mod.make_ag_request(1234, "tok", "Method", {})
        self.assertEqual(res, {"status": "ok"})

    @patch("limitlens.providers.antigravity.make_ag_request")
    def test_make_ag_request_with_tls_fallback(self, mock_req):
        mock_req.return_value = {"ok": 1}
        res, insecure = ag_mod.make_ag_request_with_tls_fallback(1234, "tok", "M", {})
        self.assertEqual(res, {"ok": 1})
        self.assertFalse(insecure)

        mock_req.side_effect = [ssl.SSLCertVerificationError(), {"ok": 2}]
        res, insecure = ag_mod.make_ag_request_with_tls_fallback(1234, "tok", "M", {})
        self.assertEqual(res, {"ok": 2})
        self.assertTrue(insecure)

    @patch("socket.create_connection")
    def test_tcp_port_open(self, mock_conn):
        self.assertTrue(ag_mod._tcp_port_open(1234))
        mock_conn.side_effect = OSError()
        self.assertFalse(ag_mod._tcp_port_open(1234))

    @patch("limitlens.providers.antigravity._tcp_port_open", return_value=True)
    @patch("limitlens.providers.antigravity.make_ag_request_with_tls_fallback")
    def test_probe_ag_port(self, mock_req, mock_tcp):
        mock_req.return_value = ({"ok": 1}, False)
        port, tok, err, ins = ag_mod.probe_ag_port([1234], "tok")
        self.assertEqual(port, 1234)
        self.assertEqual(tok, "tok")
        self.assertFalse(ins)

    @patch("limitlens.providers.antigravity.make_ag_request_with_tls_fallback")
    def test_get_ag_model_quotas(self, mock_req):
        mock_req.return_value = ({"userStatus": {"cascadeModelConfigData": {"clientModelConfigs": [{"label": "m1", "quotaInfo": {"remainingFraction": 0.5}}]}}}, False)
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(models[0]["label"], "m1")
        self.assertEqual(models[0]["pct_left"], 50.0)

    def test_cache(self):
        with patch("os.path.exists", return_value=False):
            self.assertEqual(ag_mod.load_antigravity_cache(), {"profiles": {}})

        m = mock_open(read_data='{"profiles": {"a": 1}}')
        with patch("builtins.open", m), patch("os.path.exists", return_value=True):
            self.assertEqual(ag_mod.load_antigravity_cache(), {"profiles": {"a": 1}})

        m = mock_open()
        with patch("builtins.open", m), patch("os.replace"), patch("os.makedirs"):
            ag_mod.save_antigravity_cache({"profiles": {}})
            self.assertIsNone(ag_mod.try_save_antigravity_cache({"profiles": {}}))

    def test_format_stale_message(self):
        now = datetime.now(timezone.utc)
        self.assertIn("ago", ag_mod.format_stale_message(now.isoformat(), "reason"))

    def test_apply_antigravity_cached_fallback(self):
        prof_data = {"name": "p1"}
        cache = {"profiles": {"p1": {"models": [{}], "fetched_at": "time"}}}
        self.assertTrue(ag_mod.apply_antigravity_cached_fallback(prof_data, cache, "r"))
        self.assertEqual(prof_data["status"], "stale")

    @patch("limitlens.providers.antigravity.platform.system", return_value="Windows")
    def test_get_antigravity_data_windows(self, mock_sys):
        res = ag_mod.get_antigravity_data(MagicMock())
        self.assertIn("error", res)

    @patch("limitlens.providers.antigravity.get_antigravity_named_profiles", return_value=([], None))
    @patch("limitlens.providers.antigravity.discover_active_cli_profiles", return_value={})
    @patch("limitlens.providers.antigravity.as_completed", return_value=[])
    def test_get_antigravity_data_no_profiles(self, mock_as_completed, mock_cli, mock_named):
        res = ag_mod.get_antigravity_data(MagicMock())
        self.assertIn("error", res)

    def test_display_antigravity_text(self):
        args = MagicMock()
        args.verbose = True
        args.no_color = True

        # Error case
        ag_mod.display_antigravity_text({"error": "some err"}, args)

        # Valid data
        data = {
            "profiles": [
                {
                    "name": "ide",
                    "status": "running",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "models": [
                        {"label": "model1", "pct_left": 100, "visible": True}
                    ]
                }
            ]
        }
        ag_mod.display_antigravity_text(data, args)




class TestMoreAntigravityExtra(unittest.TestCase):
    @patch("limitlens.providers.antigravity.platform.system", return_value="Darwin")
    @patch("limitlens.providers.antigravity.get_antigravity_named_profiles", return_value=([], None))
    @patch("limitlens.providers.antigravity.discover_active_cli_profiles", return_value={})
    @patch("limitlens.providers.antigravity.as_completed")
    def test_get_antigravity_data_no_profiles_fix(self, mock_as_completed, mock_cli, mock_named, mock_platform):
        # We test the "if not data: return {'error': 'no profiles found'}" branch
        mock_as_completed.return_value = []
        res = ag_mod.get_antigravity_data(MagicMock())
        self.assertEqual(res, {"error": "no profiles found"})

    @patch("subprocess.run")
    def test_collect_listening_ports_exceptions(self, mock_run):
        # ss exception
        mock_run.side_effect = OSError("ss failed")
        ports, err = ag_mod.collect_listening_ports(["123"], "Linux")
        self.assertEqual(ports, [])
        self.assertIn("ss failed", err)

        # lsof exception
        mock_run.side_effect = OSError("lsof failed")
        ports, err = ag_mod.collect_listening_ports(["123"], "Darwin")
        self.assertEqual(ports, [])
        self.assertIn("lsof failed", err)

        # lsof return code != 0
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "lsof err"
        mock_run.side_effect = None
        mock_run.return_value = mock_proc
        ports, err = ag_mod.collect_listening_ports(["123"], "Darwin")
        self.assertEqual(ports, [])
        self.assertIn("lsof err", err)

    @patch("subprocess.run")
    def test_find_language_server_for_profile_exceptions(self, mock_run):
        # ps exception
        mock_run.side_effect = OSError("ps failed")
        token, ports, err, tokens = ag_mod.find_language_server_for_profile("prof", "Darwin")
        self.assertIn("ps failed", err)
        self.assertIsNone(token)

        # test parent process lookup for Electron
        mock_run.side_effect = None
        mock_proc1 = MagicMock()
        mock_proc1.returncode = 0
        mock_proc1.stdout = "100 Electron AntigravityProfiles/prof/\n"

        mock_proc2 = MagicMock()
        mock_proc2.returncode = 0
        mock_proc2.stdout = "101 100 Helper\n102 101 language_server_macos --https_server_port 1234 --csrf_token abc\n"

        mock_run.side_effect = [mock_proc1, mock_proc2]
        token, ports, err, tokens = ag_mod.find_language_server_for_profile("prof", "Darwin")
        self.assertEqual(token, "abc")
        self.assertEqual(ports, [1234])

    @patch("subprocess.run")
    def test_find_language_server_for_main_profile_logic(self, mock_run):
        mock_run.side_effect = OSError("ps failed")
        token, ports, err, tokens = ag_mod.find_language_server_for_main_profile("Darwin", [])
        self.assertIn("ps failed", err)

        mock_run.side_effect = None
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        # Check exclusion logic:
        # short parts
        # not matching bin
        # matching profile
        # parent in profile tree
        mock_proc.stdout = """
100 1 Electron AntigravityProfiles/other/
101 100 Helper
102 101 language_server_macos /extensions/antigravity/bin/ --https_server_port 1111 --csrf_token aaa
103 1 language_server_macos /extensions/antigravity/bin/ --https_server_port 2222 --csrf_token bbb
104 1 short
105 1 language_server_macos other/bin --https_server_port 3333 --csrf_token ccc
"""
        mock_run.return_value = mock_proc
        token, ports, err, tokens = ag_mod.find_language_server_for_main_profile("Darwin", [])
        self.assertEqual(token, "bbb")
        self.assertEqual(ports, [2222])

    def test_get_config_dir_for_pid_exceptions(self):
        # Linux OS Error
        with patch("builtins.open", side_effect=OSError), patch("os.listdir", side_effect=OSError):
            self.assertIsNone(ag_mod.get_config_dir_for_pid("123", "Linux"))

        # Mac OS Error
        with patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(ag_mod.get_config_dir_for_pid("123", "Darwin"))

    @patch("subprocess.run")
    def test_discover_active_cli_profiles_exceptions(self, mock_run):
        mock_run.side_effect = OSError("ps failed")
        self.assertEqual(ag_mod.discover_active_cli_profiles("Darwin"), {})

        # short parts
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100 1\n"
        mock_run.side_effect = None
        mock_run.return_value = mock_proc
        self.assertEqual(ag_mod.discover_active_cli_profiles("Darwin"), {})

    @patch("subprocess.run")
    def test_find_language_server_for_cli_exceptions(self, mock_run):
        mock_run.side_effect = OSError("ps failed")
        token, ports, err, tokens = ag_mod.find_language_server_for_cli("Darwin")
        self.assertIn("ps failed", err)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "100\n" # short parts
        mock_run.side_effect = None
        mock_run.return_value = mock_proc
        with patch("os.path.exists", return_value=True):
            token, ports, err, tokens = ag_mod.find_language_server_for_cli("Darwin")
            self.assertIn("agy CLI not running", err)

    def test_format_ag_error_more(self):
        # urllib reason
        err = urllib.error.URLError("some reason")
        self.assertEqual(ag_mod.format_ag_error(err), "some reason")

    @patch("urllib.request.urlopen")
    def test_make_ag_request_unverified(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = ag_mod.make_ag_request(1234, "tok", "Method", {}, verify_tls=False)
        self.assertEqual(res, {"status": "ok"})

    @patch("limitlens.providers.antigravity.make_ag_request")
    def test_make_ag_request_with_tls_fallback_fallthrough(self, mock_req):
        mock_req.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            ag_mod.make_ag_request_with_tls_fallback(1234, "tok", "M", {})

    def test_is_localhost(self):
        self.assertTrue(ag_mod.is_localhost("127.0.0.1"))
        self.assertTrue(ag_mod.is_localhost("localhost"))
        self.assertTrue(ag_mod.is_localhost("::1"))
        self.assertFalse(ag_mod.is_localhost("example.com"))
        self.assertFalse(ag_mod.is_localhost("8.8.8.8"))
        self.assertFalse(ag_mod.is_localhost(None))

    @patch("urllib.request.urlopen")
    def test_make_ag_request_restrict_tls_bypass(self, mock_urlopen):
        with self.assertRaises(ValueError):
            ag_mod.make_ag_request(1234, "tok", "Method", {}, verify_tls=False, host="example.com")

    @patch("limitlens.providers.antigravity.make_ag_request")
    def test_make_ag_request_with_tls_fallback_restricted(self, mock_req):
        mock_req.side_effect = ssl.SSLCertVerificationError()
        with self.assertRaises(ssl.SSLError):
            ag_mod.make_ag_request_with_tls_fallback(1234, "tok", "M", {}, host="example.com")

    @patch("limitlens.providers.antigravity.make_ag_request")
    @patch("limitlens.providers.antigravity.log_security_warning")
    def test_make_ag_request_with_tls_fallback_allowed_with_warning(self, mock_warn, mock_req):
        mock_req.side_effect = [ssl.SSLCertVerificationError(), {"ok": 2}]
        res, insecure = ag_mod.make_ag_request_with_tls_fallback(1234, "tok", "M", {}, host="127.0.0.1")
        self.assertEqual(res, {"ok": 2})
        self.assertTrue(insecure)
        mock_warn.assert_called_once()

    @patch("limitlens.providers.antigravity._tcp_port_open", return_value=True)
    @patch("limitlens.providers.antigravity.make_ag_request_with_tls_fallback")
    def test_probe_ag_port_exceptions(self, mock_req, mock_tcp):
        mock_req.side_effect = TimeoutError("timeout")
        port, tok, err, ins = ag_mod.probe_ag_port([1234], "tok")
        self.assertIsNone(port)
        self.assertIn("request timed out", err)

        mock_tcp.return_value = False
        port, tok, err, ins = ag_mod.probe_ag_port([1234], "tok")
        self.assertIn("TCP closed", err)

    @patch("limitlens.providers.antigravity.make_ag_request_with_tls_fallback")
    def test_get_ag_model_quotas_exceptions(self, mock_req):
        # HTTPError token
        mock_err = urllib.error.HTTPError("url", 401, "Auth", {}, None)
        mock_err.read = MagicMock(return_value=b'token missing')
        mock_req.side_effect = mock_err
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(err, "not_signed_in")

        # HTTPError other
        mock_err2 = urllib.error.HTTPError("url", 500, "Err", {}, None)
        mock_err2.read = MagicMock(return_value=b'internal error')
        mock_req.side_effect = mock_err2
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(err, "internal error")

        # General URLError
        mock_req.side_effect = TimeoutError()
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(err, "request timed out")

        # Message code with token
        mock_req.side_effect = None
        mock_req.return_value = ({"code": 1, "message": "token expired"}, False)
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(err, "not_signed_in")

        # Message code general
        mock_req.return_value = ({"code": 1, "message": "general error"}, False)
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertEqual(err, "general error")

        # TypeError in parsing
        mock_req.return_value = ({"userStatus": {"cascadeModelConfigData": "invalid"}}, False)
        models, err, ins = ag_mod.get_ag_model_quotas(1234, "tok")
        self.assertIn("Error parsing", err)

    def test_cache_exceptions(self):
        m = mock_open(read_data='invalid json')
        with patch("builtins.open", m), patch("os.path.exists", return_value=True):
            self.assertEqual(ag_mod.load_antigravity_cache(), {"profiles": {}})

        m2 = mock_open(read_data='["not a dict"]')
        with patch("builtins.open", m2), patch("os.path.exists", return_value=True):
            self.assertEqual(ag_mod.load_antigravity_cache(), {"profiles": {}})

        m3 = mock_open(read_data='{"profiles": ["not a dict"]}')
        with patch("builtins.open", m3), patch("os.path.exists", return_value=True):
            self.assertEqual(ag_mod.load_antigravity_cache(), {"profiles": {}})

        with patch("builtins.open", mock_open()), patch("os.replace", side_effect=Exception("err")), patch("os.makedirs"), patch("os.path.exists", return_value=True), patch("os.unlink"):
            with self.assertRaises(Exception):
                ag_mod.save_antigravity_cache({"profiles": {}})

    @patch("limitlens.providers.antigravity.find_language_server_for_main_profile")
    @patch("limitlens.providers.antigravity.find_language_server_for_profile")
    @patch("limitlens.providers.antigravity.find_language_server_for_cli")
    @patch("limitlens.providers.antigravity.probe_ag_port")
    @patch("limitlens.providers.antigravity.get_ag_model_quotas")
    def test_fetch_single_profile_full(self, mock_quota, mock_probe, mock_cli, mock_prof, mock_main):
        # 1. source="cli" without cli_info
        mock_cli.return_value = ("tok", [1], None, {})
        prof_data, cache = ag_mod._fetch_single_profile("cli_prof", "Darwin", {}, source="cli")
        self.assertEqual(prof_data["status"], "stopped")

        # 2. source="cli" with cli_info
        cli_info = {"ports": [1234], "config_dir": "/path"}
        mock_probe.return_value = (1234, "tok", None, False)
        mock_quota.return_value = ([{"label": "gemini", "pct_left": 10}], None, False)
        prof_data, cache = ag_mod._fetch_single_profile("cli_prof", "Darwin", {}, source="cli", cli_info=cli_info)
        self.assertEqual(prof_data["status"], "running")

        # 3. is_main
        mock_main.return_value = ("tok", [1234], None, {})
        mock_probe.return_value = (None, None, "probe err", False)
        prof_data, cache = ag_mod._fetch_single_profile("ide", "Darwin", {}, is_main=True)
        self.assertEqual(prof_data["status"], "stopped")

        # 4. generic profile
        mock_prof.return_value = ("tok", [1234], None, {})
        mock_probe.return_value = (1234, "tok", None, True) # insecure tls
        mock_quota.return_value = (None, "not_signed_in", False)
        prof_data, cache = ag_mod._fetch_single_profile("prof", "Darwin", {})
        self.assertEqual(prof_data["status"], "running")
        self.assertEqual(prof_data["error"], "not signed in")

        # generic profile quota err
        mock_quota.return_value = (None, "quota err", False)
        prof_data, cache = ag_mod._fetch_single_profile("prof", "Darwin", {})
        self.assertEqual(prof_data["status"], "running")
        self.assertEqual(prof_data["error"], "quota err")

        # generic profile empty models
        mock_quota.return_value = ([], None, False)
        prof_data, cache = ag_mod._fetch_single_profile("prof", "Darwin", {})
        self.assertEqual(prof_data["error"], "no model quota data")

    @patch("limitlens.providers.antigravity.section")
    @patch("limitlens.providers.antigravity.print_c")
    @patch("limitlens.providers.antigravity.identity_line")
    @patch("limitlens.providers.antigravity.print_warning")
    @patch("limitlens.providers.antigravity.print_error")
    def test_display_antigravity_text_branches(self, mock_err, mock_warn, mock_id, mock_printc, mock_sec):
        args = MagicMock()
        args.verbose = False
        args.no_color = False
        args.all = False
        args.tool = None

        # Error with warning sign
        ag_mod.display_antigravity_text({"error": "⚠ oops"}, args)
        mock_warn.assert_called()

        # Stale profile without verbose
        data = {"profiles": [{"name": "p1", "status": "stale"}]}
        ag_mod.display_antigravity_text(data, args)
        mock_printc.assert_called_with("    (all instances stopped or stale; run with --verbose to view)", "\033[90m", False)

        # Stale with verbose
        args.verbose = True
        data = {"profiles": [
            {"name": "p1", "status": "stale", "error": "not running"},
            {"name": "p2", "status": "stale", "error": "⚠ error", "warning": "warn"},
            {"name": "p3", "status": "running", "models": [{"label": "m1", "pct_left": 10, "visible": True, "reset_time": "1 day"}], "checked_at": datetime.now(timezone.utc).isoformat()},
            {"name": "p4", "status": "running", "models": [{"label": "m1", "pct_left": 10, "visible": False, "reset_time": "1 day"}]}
        ], "warning": "global warn"}
        ag_mod.display_antigravity_text(data, args)

        args.no_color = True
        ag_mod.display_antigravity_text(data, args)


class TestAntigravityIgnoredAccounts(unittest.TestCase):
    """Tests for profile_is_ignored and filter_antigravity_profiles."""

    # ── profile_is_ignored ────────────────────────────────────────────────

    def test_ignored_exact_match(self):
        self.assertTrue(profile_is_ignored("work", ["work"]))

    def test_ignored_case_insensitive(self):
        self.assertTrue(profile_is_ignored("Work", ["work"]))
        self.assertTrue(profile_is_ignored("WORK", ["Work"]))

    def test_ignored_no_match(self):
        self.assertFalse(profile_is_ignored("personal", ["work"]))

    def test_ignored_empty_list(self):
        self.assertFalse(profile_is_ignored("personal", []))

    def test_ignored_string_input(self):
        """A bare string (not a list) is treated as a single entry."""
        self.assertTrue(profile_is_ignored("ide", "ide"))

    def test_ignored_non_list_non_string(self):
        """None or other non-list values never match."""
        self.assertFalse(profile_is_ignored("ide", None))
        self.assertFalse(profile_is_ignored("ide", 42))

    def test_ignored_whitespace_stripped(self):
        self.assertTrue(profile_is_ignored("ide", ["  ide  "]))

    # ── filter_antigravity_profiles ───────────────────────────────────────

    def test_filter_no_config(self):
        named, cli = filter_antigravity_profiles(["work", "personal"], {"agy-cli": None})
        self.assertEqual(named, ["work", "personal"])
        self.assertEqual(cli, {"agy-cli": None})

    def test_filter_empty_ignored(self):
        config = {"antigravity": {"ignored_accounts": []}}
        named, cli = filter_antigravity_profiles(["work", "personal"], {"agy-cli": None}, config)
        self.assertEqual(named, ["work", "personal"])
        self.assertIn("agy-cli", cli)

    def test_filter_removes_named_profile(self):
        config = {"antigravity": {"ignored_accounts": ["work"]}}
        named, cli = filter_antigravity_profiles(["work", "personal"], {"agy-cli": None}, config)
        self.assertNotIn("work", named)
        self.assertIn("personal", named)

    def test_filter_removes_cli_profile(self):
        config = {"antigravity": {"ignored_accounts": ["agy-cli"]}}
        named, cli = filter_antigravity_profiles(["work"], {"agy-cli": None, "agy-work": None}, config)
        self.assertNotIn("agy-cli", cli)
        self.assertIn("agy-work", cli)
        self.assertEqual(named, ["work"])

    def test_filter_case_insensitive_named(self):
        config = {"antigravity": {"ignored_accounts": ["Work"]}}
        named, cli = filter_antigravity_profiles(["work", "personal"], {}, config)
        self.assertNotIn("work", named)
        self.assertIn("personal", named)

    def test_filter_removes_multiple(self):
        config = {"antigravity": {"ignored_accounts": ["work", "agy-cli"]}}
        named, cli = filter_antigravity_profiles(
            ["work", "personal"], {"agy-cli": None, "agy-work": None}, config
        )
        self.assertEqual(named, ["personal"])
        self.assertEqual(list(cli.keys()), ["agy-work"])

    def test_filter_all_ignored_returns_empty(self):
        config = {"antigravity": {"ignored_accounts": ["work", "personal"]}}
        named, cli = filter_antigravity_profiles(["work", "personal"], {}, config)
        self.assertEqual(named, [])
        self.assertEqual(cli, {})

    def test_filter_missing_antigravity_key(self):
        """Config without an antigravity section passes everything through."""
        named, cli = filter_antigravity_profiles(["work"], {"agy-cli": None}, {"codex": {}})
        self.assertEqual(named, ["work"])
        self.assertIn("agy-cli", cli)


if __name__ == "__main__":
    unittest.main()
