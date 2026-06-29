import sys
import json
import pytest
import subprocess
from unittest.mock import patch, MagicMock

# Mock rumps BEFORE importing limitlens.menubar
mock_rumps = MagicMock()
class MockMenuItem:
    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback
        self.items = []

    def add(self, item):
        self.items.append(item)

    def __str__(self):
        child_text = "\n".join(str(item) for item in self.items)
        return self.title if not child_text else f"{self.title}\n{child_text}"

class MockApp:
    def __init__(self, title, *args, **kwargs):
        self.title = title
        self.menu = MagicMock()
        self.menu.clear = MagicMock()
        self.menu.add = MagicMock()

mock_rumps.App = MockApp
mock_rumps.MenuItem = MockMenuItem
mock_rumps.separator = "---"
mock_rumps.timer = lambda x: (lambda f: f)
mock_rumps.clicked = lambda x: (lambda f: f)
sys.modules['rumps'] = mock_rumps

import limitlens.menubar  # noqa: E402
from limitlens.menubar import LimitLensApp  # noqa: E402

@pytest.fixture
def app():
    return LimitLensApp()

def test_init(app):
    assert app.title == "⏳ Loading..."
    assert len(app.menu) == 4
    assert not app._is_fetching
    assert app._queued_sync_codex is None
    assert app._pending_title is None

def test_refresh_and_on_refresh(app):
    with patch.object(app, 'fetch_data') as mock_fetch:
        app.refresh()
        mock_fetch.assert_called_once_with()

        mock_fetch.reset_mock()
        app._on_refresh(None)
        mock_fetch.assert_called_once_with(sync_codex=True)

        mock_fetch.reset_mock()
        app._on_quick_refresh(None)
        mock_fetch.assert_called_once_with(sync_codex=False)

def test_check_updates(app):
    app._pending_title = "New Title"
    app.menu = MagicMock()
    app._pending_menu_items = ["Item 1", "Item 2"]

    app.check_updates(None)

    assert app.title == "New Title"
    assert app._pending_title is None
    assert app._pending_menu_items is None
    app.menu.clear.assert_called_once()
    app.menu.add.assert_any_call("Item 1")
    app.menu.add.assert_any_call("Item 2")

    app._pending_menu_items = []
    app.menu.clear.reset_mock()
    app.menu.add.reset_mock()

    app.check_updates(None)
    # The 'No active quotas found' logic is in _refresh_sync,
    # check_updates simply loops pending_menu_items. Since it's empty, it won't add it directly here.
    # We just ensure it clears and adds the basic items.
    app.menu.clear.assert_called_once()

def test_notify(app):
    with patch("subprocess.Popen") as mock_popen:
        app.notify("Test Title", "Test Msg")
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "osascript"
        assert "Test Msg" in args
        assert "Test Title" in args


def test_open_config_creates_missing_config_then_opens(app):
    with patch("limitlens.config.limitlens_config_path", return_value="/tmp/limitlens-test-config.json"), \
         patch("limitlens.menubar.os.path.exists", return_value=False), \
         patch("limitlens.config.auto_detect_providers") as mock_auto_detect, \
         patch("subprocess.Popen") as mock_popen:
        app._on_open_config(None)

    mock_auto_detect.assert_called_once_with("/tmp/limitlens-test-config.json", write=True, interactive=False)
    mock_popen.assert_called_once_with(["open", "/tmp/limitlens-test-config.json"])


def test_build_menu_items_returns_submenu_spec_not_rumps_item(app):
    row = app._row("Amp", "Amp", "Credits", 50.0, remaining=5, total=10, unit="$")
    app._last_refresh_label = "7:00 PM"

    with patch("limitlens.menubar.rumps.MenuItem", side_effect=AssertionError("worker must not create menu items")):
        items = app._build_menu_items({"recommendations": {"hard": []}}, [row])

    submenu_specs = [item for item in items if isinstance(item, tuple) and item[:2] == ("submenu", "All Quotas")]
    assert len(submenu_specs) == 1
    assert "Amp" in "\n".join(submenu_specs[0][2])


def test_build_menu_items_groups_dropdown_like_tabs(app):
    row = app._row("Amp", "Amp", "Credits", 50.0, remaining=5, total=10, unit="$")
    app._last_refresh_label = "7:00 PM"

    items = app._build_menu_items({"recommendations": {"hard": []}}, [row])
    submenu_titles = [item[1] for item in items if isinstance(item, tuple) and item[0] == "submenu"]

    assert submenu_titles == ["Overview", "All Quotas", "Doctor", "Actions"]
    overview = next(item for item in items if isinstance(item, tuple) and item[:2] == ("submenu", "Overview"))
    doctor = next(item for item in items if isinstance(item, tuple) and item[:2] == ("submenu", "Doctor"))
    actions = next(item for item in items if isinstance(item, tuple) and item[:2] == ("submenu", "Actions"))
    assert "Last refreshed: 7:00 PM" in overview[2]
    assert "Run `limitlens doctor`" in doctor[2]
    assert "Refresh" in "\n".join(str(item) for item in actions[2])


def test_copy_status_uses_readable_summary(app):
    app._last_status_summary = "LimitLens status\nRecommended:\n- Amp · 50%"
    with patch("subprocess.run") as mock_run:
        app._on_copy_status(None)

    mock_run.assert_called_once_with(
        ["pbcopy"],
        input=app._last_status_summary,
        text=True,
        check=False,
        timeout=5,
    )
    assert app._pending_title == "✓ Status copied"

def test_fetch_data_already_fetching(app):
    import threading
    app._is_fetching = True
    app._fetch_lock = threading.Lock()
    with patch("threading.Thread") as mock_thread:
        app.fetch_data()
        mock_thread.assert_not_called()
        assert app._queued_sync_codex is False

        app.fetch_data(sync_codex=True)
        mock_thread.assert_not_called()
        assert app._queued_sync_codex is True


def test_fetch_data_sets_refreshing_state(app):
    with patch("threading.Thread") as mock_thread:
        app.fetch_data()

    assert app._pending_title == "↻ Refreshing…"
    mock_thread.assert_called_once()


def test_fetch_data_allows_slow_normal_refresh(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"recommendations": {"hard": []}})

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc) as mock_run:

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()

        mock_thread.side_effect = mock_thread_init

        app.fetch_data()

    assert mock_run.call_args.kwargs["timeout"] >= 30


def test_fetch_data_runs_queued_manual_refresh_after_current_fetch(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"recommendations": {"hard": []}})
    original_fetch = app.fetch_data

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc) as mock_run:

        calls = 0

        def mock_thread_init(target, daemon):
            nonlocal calls
            calls += 1
            if calls == 1:
                app._queued_sync_codex = True
            target()
            return MagicMock()

        mock_thread.side_effect = mock_thread_init
        original_fetch()

    assert mock_thread.call_count == 2
    assert mock_run.call_count == 2
    second_cmd = mock_run.call_args_list[1].args[0]
    assert "--sync-codex" in second_cmd
    assert app._queued_sync_codex is None


def test_refresh_actions_use_expected_codex_sync_modes(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps({"recommendations": {"hard": []}})

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc) as mock_run:

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()

        mock_thread.side_effect = mock_thread_init

        app._on_refresh(None)
        refresh_cmd = mock_run.call_args.args[0]
        assert "--sync-codex" in refresh_cmd

        mock_run.reset_mock()
        app._on_quick_refresh(None)
        quick_cmd = mock_run.call_args.args[0]
        assert "--sync-codex" not in quick_cmd

def test_fetch_data_success_empty(app):
    mock_data = {
        "recommendations": {"hard": []}
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(mock_data)

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()

        assert app._pending_title == "⚪ No quota"
        assert "Refresh" in "\n".join(str(item) for item in app._pending_menu_items)
        assert not app._is_fetching

def test_fetch_data_success_with_quotas(app):
    mock_data = {
        "recommendations": {
            "hard": [
                {"tool": "antigravity", "headroom_pct": 25, "name": "ag-prof:model"},
                {"tool": "antigravity", "headroom_pct": 10, "name": "ag2"},
                {"tool": "codex", "headroom_pct": 5, "name": "codex-foo"},
                {"tool": "codex", "headroom_pct": 15, "name": "codex-bar (info)"},
                {"tool": "other", "headroom_pct": 15, "name": "profile:codex-xyz → the-model-name something"},
            ]
        },
        "codex": {
            "accounts": [
                {"name": "Acc1", "limits": [{"label": "Reqs", "left_percent": 5.0}]},
                {"error": "some error"}
            ]
        },
        "amp": {
            "tiers": [
                {"label": "Tier1", "pct_left": 8.0, "remaining": 1.5, "total": 10.0}
            ]
        },
        "antigravity": {
            "profiles": [
                {
                    "name": "prof1",
                    "status": "running",
                    "models": [{"label": "m1", "pct_left": 20.0}]
                },
                {
                    "name": "prof2",
                    "status": "stopped",
                    "models": [{"label": "m2", "pct_left": 5.0}]
                }
            ]
        },
        "opencode": {
            "credit_limits": [
                {"name": "Limit1", "pct_left": 15.0, "remaining": 50, "total": 100, "unit": "USD"},
                {"name": "Limit2", "pct_left": 5.0, "remaining": 10, "total": 100, "unit": "credits"}
            ]
        },
        "pioneer": {
            "tiers": [
                {"label": "P1", "pct_left": 2.0}
            ]
        },
        "cursor": {
            "tiers": [
                {"label": "C1", "pct_left": 50.0, "used": 50},
                {"label": "C2", "pct_left": None, "used": 120}
            ]
        }
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(mock_data)

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc), \
         patch.object(app, 'notify') as mock_notify:

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init
        app._has_loaded_once = True

        app.fetch_data()

        assert "🪐25%" in app._pending_title
        assert "🪐10%" in app._pending_title
        assert "+3" in app._pending_title
        assert "ag-prof" not in app._pending_title  # title stays tiny; details are in dropdown

        menu = app._pending_menu_items
        menu_text = "\n".join(str(item) for item in menu)
        assert "✨ Recommended" in menu_text
        assert "ag-prof:model" in menu_text
        assert "25%" in menu_text
        assert "⚠️ Low quota" in menu_text
        assert "All Quotas" in menu_text
        assert "Last refreshed:" in menu_text
        assert "Codex" in menu_text and "Acc1" in menu_text and "Reqs" in menu_text and "5%" in menu_text
        assert "Amp" in menu_text and "Tier1" in menu_text and "$1.50/$10.00" in menu_text
        assert "Antigrav" in menu_text and "prof1" in menu_text and "m1" in menu_text and "20%" in menu_text
        assert "Antigrav" in menu_text and "prof2" in menu_text and "m2" in menu_text and "stopped" in menu_text
        assert "OpenCode" in menu_text and "Limit1" in menu_text and "$50.00/$100.00" in menu_text
        assert "OpenCode" in menu_text and "Limit2" in menu_text and "10/100 credits" in menu_text
        assert "Pioneer" in menu_text and "P1" in menu_text and "2%" in menu_text
        assert "Cursor" in menu_text and "C1" in menu_text and "50%" in menu_text
        assert "Cursor" in menu_text and "C2" in menu_text and "120 used" in menu_text
        assert "Refresh" in menu_text
        assert "Quick Refresh" in menu_text
        assert "Open Config" in menu_text
        assert "Copy Status" in menu_text
        assert "Recommended:" in app._last_status_summary
        assert "Low quota:" in app._last_status_summary

        mock_notify.assert_any_call("LimitLens Quota Warning", "Codex (Acc1) Reqs is running low (5.0% left).")
        mock_notify.assert_any_call("LimitLens Quota Warning", "Tier1 is running low (8.0% left). ($1.50 remaining)")
        mock_notify.assert_any_call("LimitLens Quota Warning", "OpenCode (Limit2) is running low (5.0% left). (10.00 remaining)")
        mock_notify.assert_any_call("LimitLens Quota Warning", "P1 is running low (2.0% left).")

        mock_notify.reset_mock()
        mock_data["codex"]["accounts"][0]["limits"][0]["left_percent"] = 20.0
        mock_proc.stdout = json.dumps(mock_data)

        app.fetch_data()

        mock_notify.assert_not_called()
        assert "codex-Acc1-Reqs" not in app._notified_set


def test_fetch_data_groups_antigravity_5h_and_weekly_rows(app):
    mock_data = {
        "recommendations": {"hard": []},
        "antigravity": {
            "profiles": [
                {
                    "name": "main",
                    "status": "running",
                    "models": [
                        {"label": "Gemini", "limit_type": "5h window", "pct_left": 80.0},
                        {"label": "Gemini", "limit_type": "weekly", "pct_left": 40.0},
                    ],
                }
            ]
        },
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(mock_data)

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()

        mock_thread.side_effect = mock_thread_init
        app.fetch_data()

    menu_text = "\n".join(str(item) for item in app._pending_menu_items)
    assert menu_text.count("Gemini") == 1
    assert "h 80%" in menu_text
    assert "w 40%" in menu_text
    assert "Gemini (5h)" not in menu_text
    assert "Gemini (weekly)" not in menu_text

def test_fetch_data_suppresses_initial_low_quota_notification(app):
    mock_data = {
        "recommendations": {"hard": []},
        "codex": {
            "accounts": [
                {"name": "Acc1", "limits": [{"label": "Reqs", "left_percent": 5.0}]}
            ]
        },
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(mock_data)

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc), \
         patch.object(app, 'notify') as mock_notify:

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()
        mock_notify.assert_not_called()
        assert app._has_loaded_once

        app.fetch_data()
        mock_notify.assert_called_once_with(
            "LimitLens Quota Warning",
            "Codex (Acc1) Reqs is running low (5.0% left).",
        )


def test_fetch_data_subprocess_error(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "some error\nlast error line"

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()
        assert app._pending_title == "⚠️ last error line"

def test_fetch_data_subprocess_timeout(app):
    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=15)):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()
        assert app._pending_title == "⚠️ Timeout"

def test_fetch_data_subprocess_exception(app):
    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", side_effect=ValueError("some bad error")):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()
        assert app._pending_title == "⚠️ some bad error"

def test_main():
    with patch("limitlens.menubar.sys.platform", "darwin"), \
         patch("limitlens.menubar.LimitLensApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app

        limitlens.menubar.main()

        mock_app_cls.assert_called_once()
        mock_app.fetch_data.assert_called_once()
        mock_app.check_updates.assert_called_once_with(None)
        mock_app.run.assert_called_once()


def test_fetch_data_logging(app):
    import os
    import shutil
    log_dir = os.path.expanduser("~/.cache/limitlens")
    log_file = os.path.join(log_dir, "limitlens.log")

    backup_log_file = log_file + ".bak"
    if os.path.exists(log_file):
        shutil.copy2(log_file, backup_log_file)
        os.remove(log_file)

    try:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some process failure error"

        with patch("threading.Thread") as mock_thread, \
             patch("subprocess.run", return_value=mock_proc):

            def mock_thread_init(target, daemon):
                target()
                return MagicMock()
            mock_thread.side_effect = mock_thread_init

            app.fetch_data()

        assert os.path.exists(log_file)
        with open(log_file, "r") as f:
            content = f.read()
            assert "Menubar command failure" in content
            assert "some process failure error" in content

        os.remove(log_file)

        with patch("threading.Thread") as mock_thread, \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=15)):

            def mock_thread_init(target, daemon):
                target()
                return MagicMock()
            mock_thread.side_effect = mock_thread_init

            app.fetch_data()

        assert os.path.exists(log_file)
        with open(log_file, "r") as f:
            content = f.read()
            assert "Menubar subprocess timeout" in content

        os.remove(log_file)

        with patch("threading.Thread") as mock_thread, \
             patch("subprocess.run", side_effect=ValueError("another bad error")):

            def mock_thread_init(target, daemon):
                target()
                return MagicMock()
            mock_thread.side_effect = mock_thread_init

            app.fetch_data()

        assert os.path.exists(log_file)
        with open(log_file, "r") as f:
            content = f.read()
            assert "Menubar exception" in content
            assert "another bad error" in content

    finally:
        if os.path.exists(log_file):
            os.remove(log_file)
        if os.path.exists(backup_log_file):
            shutil.move(backup_log_file, log_file)


def test_fetch_data_failure_updates_status_summary(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "provider boom"

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()

    assert app._pending_title == "⚠️ provider boom"
    assert "Refresh failed" in app._last_status_summary
    assert "provider boom" in app._last_status_summary
    assert app._pending_menu_items
    assert any("Refresh failed" in str(item) for item in app._pending_menu_items)


def test_fetch_data_failure_redacts_copyable_status(app):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stderr = "failed /Users/tester/.config/limitlens/config.json user@example.com token=secret123"

    with patch("threading.Thread") as mock_thread, \
         patch("subprocess.run", return_value=mock_proc):

        def mock_thread_init(target, daemon):
            target()
            return MagicMock()
        mock_thread.side_effect = mock_thread_init

        app.fetch_data()

    visible = app._last_status_summary + "\n" + "\n".join(str(item) for item in app._pending_menu_items)
    assert "/Users/tester" not in visible
    assert "user@example.com" not in visible
    assert "secret123" not in visible
    assert "token=<redacted>" in visible
