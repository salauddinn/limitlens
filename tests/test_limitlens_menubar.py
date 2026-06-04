import sys
import json
import pytest
import subprocess
from unittest.mock import patch, MagicMock

# Mock rumps BEFORE importing limitlens.menubar
mock_rumps = MagicMock()
class MockApp:
    def __init__(self, title, *args, **kwargs):
        self.title = title
        self.menu = MagicMock()
        self.menu.clear = MagicMock()
        self.menu.add = MagicMock()

mock_rumps.App = MockApp
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
    assert len(app.menu) == 3
    assert not app._is_fetching
    assert app._pending_title is None

def test_refresh_and_on_refresh(app):
    with patch.object(app, 'fetch_data') as mock_fetch:
        app.refresh()
        mock_fetch.assert_called_once()
        
        mock_fetch.reset_mock()
        app._on_refresh(None)
        mock_fetch.assert_called_once()

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

def test_fetch_data_already_fetching(app):
    app._is_fetching = True
    with patch("threading.Thread") as mock_thread:
        app.fetch_data()
        mock_thread.assert_not_called()

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
        assert app._pending_menu_items == []
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
        assert "[Best available]" in menu
        assert "ag-prof:model" in menu_text
        assert "25.0%" in menu_text
        assert "[Usage overview]" in menu
        assert "Codex" in menu_text and "Acc1" in menu_text and "Reqs" in menu_text and "5.0%" in menu_text
        assert "Amp" in menu_text and "Tier1" in menu_text and "$1.50/$10.00" in menu_text
        assert "Antigrav" in menu_text and "prof1" in menu_text and "m1" in menu_text and "20.0%" in menu_text
        assert "Antigrav" in menu_text and "prof2" in menu_text and "m2" in menu_text and "stopped" in menu_text
        assert "OpenCode" in menu_text and "Limit1" in menu_text and "$50.00/$100.00" in menu_text
        assert "OpenCode" in menu_text and "Limit2" in menu_text and "10/100 credits" in menu_text
        assert "Pioneer" in menu_text and "P1" in menu_text and "2.0%" in menu_text
        assert "Cursor" in menu_text and "C1" in menu_text and "50.0%" in menu_text
        assert "Cursor" in menu_text and "C2" in menu_text and "120 used" in menu_text
        
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
