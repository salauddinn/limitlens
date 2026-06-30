import argparse
from unittest.mock import MagicMock, patch

from limitlens.providers.cline import (
    display_cline_text,
    fetch_usage_limits,
    get_cline_data,
)


USAGE_SAMPLE = {
    "data": {
        "limits": [
            {"type": "five_hour", "percentUsed": 7, "resetsAt": "2026-06-30T07:56:18Z"},
            {"type": "weekly", "percentUsed": 4, "resetsAt": "2026-07-06T17:36:47Z"},
            {"type": "monthly", "percentUsed": 2, "resetsAt": "2026-07-29T17:36:47Z"},
        ]
    },
    "success": True,
}


def test_fetch_usage_limits_returns_data_on_success():
    resp = MagicMock()
    resp.read.return_value = __import__("json").dumps(USAGE_SAMPLE).encode()
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda self, *a: None
    with patch("limitlens.providers.cline.urllib.request.urlopen", return_value=resp):
        data = fetch_usage_limits("workos:token")
    assert data == USAGE_SAMPLE["data"]


def test_fetch_usage_limits_returns_none_on_http_error():
    import urllib.error

    with patch(
        "limitlens.providers.cline.urllib.request.urlopen",
        side_effect=urllib.error.URLError("boom"),
    ):
        assert fetch_usage_limits("workos:token") is None


def test_fetch_usage_limits_none_token():
    assert fetch_usage_limits(None) is None


def test_get_cline_data_with_quota_windows():
    with patch("limitlens.providers.cline.shutil.which", return_value="/usr/bin/cline"), \
         patch("limitlens.providers.cline._cline_version", return_value="3.0.34"), \
         patch("limitlens.providers.cline._load_stored_credentials", return_value={"access": "workos:t"}), \
         patch("limitlens.providers.cline._token_expired", return_value=False), \
         patch("limitlens.providers.cline.fetch_usage_limits", return_value=USAGE_SAMPLE["data"]):
        data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": True}})

    assert data["installed"] is True
    assert data["version"] == "3.0.34"
    assert len(data["windows"]) == 3
    five_h = data["windows"][0]
    assert five_h["label"] == "5h"
    assert five_h["pct_left"] == 93.0
    assert data["windows"][1]["label"] == "weekly"
    assert data["windows"][2]["label"] == "monthly"


def test_get_cline_data_disabled_returns_none():
    data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": False}})
    assert data is None


def test_get_cline_data_direct_tool_bypasses_disabled():
    with patch("limitlens.providers.cline.shutil.which", return_value="/usr/bin/cline"), \
         patch("limitlens.providers.cline._cline_version", return_value="3.0.34"), \
         patch("limitlens.providers.cline._load_stored_credentials", return_value=None), \
         patch("limitlens.providers.cline._resolve_access_token", return_value=None):
        data = get_cline_data(argparse.Namespace(tool="cline"), {"cline": {"enabled": False}})
    assert data["installed"] is True
    assert "windows" not in data
    assert "sign in" in data["note"]


def test_get_cline_data_missing_cli_and_no_token():
    with patch("limitlens.providers.cline.shutil.which", return_value=None), \
         patch("limitlens.providers.cline._load_stored_credentials", return_value=None), \
         patch("limitlens.providers.cline._resolve_access_token", return_value=None):
        data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": True}})
    assert data["installed"] is False
    assert data["status"] == "not installed"


def test_get_cline_data_signed_out_reports_note():
    with patch("limitlens.providers.cline.shutil.which", return_value="/usr/bin/cline"), \
         patch("limitlens.providers.cline._cline_version", return_value="3.0.34"), \
         patch("limitlens.providers.cline._load_stored_credentials", return_value=None), \
         patch("limitlens.providers.cline._resolve_access_token", return_value=None):
        data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": True}})
    assert data["installed"] is True
    assert data["status"] == "installed"
    assert "sign in" in data["note"]


def test_display_cline_text_renders_windows(capsys):
    args = argparse.Namespace(no_color=True, verbose=False, all=False, tool=None)
    display_cline_text({
        "name": "Cline CLI",
        "installed": True,
        "version": "3.0.34",
        "status": "installed",
        "windows": [
            {"type": "five_hour", "label": "5h", "pct_used": 7.0, "pct_left": 93.0, "resets_at": "2026-06-30T07:56:18Z"},
            {"type": "weekly", "label": "weekly", "pct_used": 4.0, "pct_left": 96.0, "resets_at": "2026-07-06T17:36:47Z"},
            {"type": "monthly", "label": "monthly", "pct_used": 2.0, "pct_left": 98.0, "resets_at": "2026-07-29T17:36:47Z"},
        ],
    }, args)
    out = capsys.readouterr().out
    assert "Cline CLI" in out
    assert "ClinePass" in out
    assert "5h" in out and "weekly" in out and "monthly" in out
    assert "93%" in out and "96%" in out and "98%" in out
    assert "left to reset" in out


def test_display_cline_text_falls_back_to_status(capsys):
    args = argparse.Namespace(no_color=True, verbose=False, all=False, tool="cline")
    display_cline_text({
        "name": "Cline CLI",
        "installed": True,
        "version": "3.0.34",
        "status": "installed",
        "note": "sign in with `cline auth` to fetch ClinePass quota",
    }, args)
    out = capsys.readouterr().out
    assert "status: installed" in out
    assert "sign in" in out
