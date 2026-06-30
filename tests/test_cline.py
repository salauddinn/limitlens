import argparse
from unittest.mock import MagicMock, patch

from limitlens.providers.cline import display_cline_text, get_cline_data


def test_get_cline_data_detects_installed_cli():
    completed = MagicMock(returncode=0, stdout="3.0.34\n", stderr="")
    with patch("limitlens.providers.cline.shutil.which", return_value="/usr/bin/cline"), \
         patch("limitlens.providers.cline.subprocess.run", return_value=completed):
        data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": True}})

    assert data == {
        "name": "Cline CLI",
        "command": "cline",
        "installed": True,
        "version": "3.0.34",
        "status": "installed",
        "note": "quota not exposed by Cline CLI",
    }


def test_get_cline_data_reports_missing_cli():
    with patch("limitlens.providers.cline.shutil.which", return_value=None):
        data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": True}})

    assert data["installed"] is False
    assert data["status"] == "not installed"


def test_get_cline_data_disabled_returns_none():
    data = get_cline_data(argparse.Namespace(), {"cline": {"enabled": False}})

    assert data is None


def test_get_cline_data_direct_tool_bypasses_disabled_default():
    completed = MagicMock(returncode=0, stdout="3.0.34\n", stderr="")
    with patch("limitlens.providers.cline.shutil.which", return_value="/usr/bin/cline"), \
         patch("limitlens.providers.cline.subprocess.run", return_value=completed):
        data = get_cline_data(argparse.Namespace(tool="cline"), {"cline": {"enabled": False}})

    assert data["installed"] is True
    assert data["version"] == "3.0.34"


def test_display_cline_text_shows_status_when_requested(capsys):
    args = argparse.Namespace(no_color=True, verbose=False, all=False, tool="cline")
    display_cline_text({
        "name": "Cline CLI",
        "command": "cline",
        "installed": True,
        "version": "3.0.34",
        "status": "installed",
        "note": "quota not exposed by Cline CLI",
    }, args)

    output = capsys.readouterr().out
    assert "Cline CLI" in output
    assert "installed" in output
    assert "3.0.34" in output
    assert "quota not exposed" in output
