import sqlite3
import urllib.request
import pytest
from unittest.mock import patch, MagicMock

from limitlens.providers.cursor import (
    _number,
    get_cursor_token,
    fetch_cursor_usage,
    parse_cursor_data,
    get_cursor_data,
    display_cursor_text,
)


class DummyArgs:
    def __init__(self, verbose=False, no_color=False):
        self.verbose = verbose
        self.no_color = no_color


def test_number():
    assert _number("1.5") == 1.5
    assert _number("2") == 2.0
    assert _number("abc") == 0.0
    assert _number("abc", default=1.0) == 1.0
    assert _number(None, default=2.0) == 2.0


def test_get_cursor_token_darwin(monkeypatch):
    with patch("os.path.exists", return_value=True):
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("test_token",)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            token = get_cursor_token("Darwin")
            assert token == "test_token"


def test_get_cursor_token_linux(monkeypatch):
    with patch("os.path.exists", return_value=True):
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = ("linux_token",)
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            token = get_cursor_token("Linux")
            assert token == "linux_token"


def test_get_cursor_token_windows(monkeypatch):
    with patch("os.path.exists", return_value=True):
        with patch("os.path.expandvars", return_value="/fake/appdata/state.vscdb"):
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = ("win_token",)
                mock_conn.cursor.return_value = mock_cursor
                mock_connect.return_value = mock_conn

                token = get_cursor_token("Windows")
                assert token == "win_token"


def test_get_cursor_token_not_found(monkeypatch):
    with patch("os.path.exists", return_value=False):
        assert get_cursor_token("Darwin") is None


def test_get_cursor_token_db_error(monkeypatch):
    with patch("os.path.exists", return_value=True):
        with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("DB error")):
            assert get_cursor_token("Darwin") is None


def test_get_cursor_token_no_row(monkeypatch):
    with patch("os.path.exists", return_value=True):
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value = mock_cursor
            mock_connect.return_value = mock_conn

            assert get_cursor_token("Darwin") is None


def test_fetch_cursor_usage_success():
    with patch("urllib.request.build_opener") as mock_build_opener:
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"data": "usage"}'
        mock_response.__enter__.return_value = mock_response
        mock_opener.open.return_value = mock_response
        mock_build_opener.return_value = mock_opener

        res = fetch_cursor_usage("token")
        assert res == {"data": "usage"}


def test_fetch_cursor_usage_error():
    with patch("urllib.request.build_opener", side_effect=Exception("Network error")):
        assert fetch_cursor_usage("token") is None


def test_fetch_cursor_usage_redirect_error():
    class MockNoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, headers):
            raise Exception("Redirects are not allowed.")

    handler = MockNoRedirectHandler()
    with pytest.raises(Exception, match="Redirects are not allowed."):
        handler.http_error_302(None, None, 302, "Found", None)


def test_parse_cursor_data_no_payload():
    res = parse_cursor_data(None, DummyArgs())
    assert "error" in res


def test_parse_cursor_data_gpt4():
    payload = {
        "startOfMonth": "2023-01-01",
        "gpt-4": {
            "numRequests": 10,
            "maxRequestUsage": 100
        }
    }
    res = parse_cursor_data(payload, DummyArgs())
    assert res["name"] == "Cursor"
    assert res["startOfMonth"] == "2023-01-01"
    tiers = res["tiers"]
    assert len(tiers) == 1
    assert tiers[0]["remaining"] == 90
    assert tiers[0]["total"] == 100
    assert tiers[0]["used"] == 10
    assert tiers[0]["pct_left"] == 90.0
    assert tiers[0]["pct_used"] == 10.0


def test_parse_cursor_data_premium():
    payload = {
        "startOfMonth": "2023-01-01",
        "premium-models": {
            "numRequests": 5,
            "maxRequestUsage": 50
        }
    }
    res = parse_cursor_data(payload, DummyArgs())
    assert res["tiers"][0]["remaining"] == 45
    assert res["tiers"][0]["total"] == 50


def test_parse_cursor_data_unlimited():
    payload = {
        "startOfMonth": "2023-01-01",
        "gpt-4": {
            "numRequests": 150
        }
    }
    res = parse_cursor_data(payload, DummyArgs())
    assert res["tiers"][0]["total"] is None
    assert res["tiers"][0]["pct_left"] is None
    assert res["tiers"][0]["used"] == 150


def test_parse_cursor_data_custom_name():
    payload = {"gpt-4": {"numRequests": 1}}
    res = parse_cursor_data(payload, DummyArgs(), cfg={"name": "CustomCursor"})
    assert res["name"] == "CustomCursor"


def test_get_cursor_data_disabled():
    res = get_cursor_data(DummyArgs(), config={"cursor": {"enabled": "false"}})
    assert res is None


@patch("limitlens.providers.cursor.get_cursor_token", return_value=None)
def test_get_cursor_data_no_token(mock_token):
    res = get_cursor_data(DummyArgs())
    assert "error" in res


@patch("limitlens.providers.cursor.get_cursor_token", return_value="token")
@patch("limitlens.providers.cursor.fetch_cursor_usage", return_value={"gpt-4": {"numRequests": 5}})
def test_get_cursor_data_success(mock_fetch, mock_token):
    res = get_cursor_data(DummyArgs())
    assert res["name"] == "Cursor"
    assert res["tiers"][0]["used"] == 5


@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_none(mock_section, mock_print_c):
    display_cursor_text(None, DummyArgs())
    mock_section.assert_not_called()


@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_error_verbose(mock_section, mock_print_c):
    args = DummyArgs(verbose=True)
    display_cursor_text({"error": "Bad error"}, args)
    mock_section.assert_called_with("Cursor", args)
    mock_print_c.assert_called()


@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_error_not_verbose(mock_section, mock_print_c):
    args = DummyArgs(verbose=False)
    display_cursor_text({"error": "Bad error"}, args)
    mock_section.assert_not_called()


@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_no_tiers(mock_section, mock_print_c):
    args = DummyArgs()
    display_cursor_text({"name": "Cursor", "tiers": []}, args)
    mock_section.assert_called()
    mock_print_c.assert_called()


@patch("limitlens.providers.cursor.bar", return_value="[bar]")
@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_with_pct(mock_section, mock_print_c, mock_bar):
    args = DummyArgs(no_color=True)
    data = {
        "name": "Cursor",
        "tiers": [{
            "label": "Requests",
            "pct_left": 50.0,
            "pct_used": 50.0,
            "used": 50,
            "total": 100,
            "unit": "req"
        }]
    }
    display_cursor_text(data, args)
    mock_section.assert_called()
    mock_print_c.assert_called()


@patch("limitlens.providers.cursor.print_c")
@patch("limitlens.providers.cursor.section")
def test_display_cursor_text_without_pct(mock_section, mock_print_c):
    args = DummyArgs()
    data = {
        "name": "Cursor",
        "tiers": [{
            "label": "Requests",
            "pct_left": None,
            "pct_used": None,
            "used": 150,
            "total": None,
            "unit": "req"
        }]
    }
    display_cursor_text(data, args)
    mock_section.assert_called()
    mock_print_c.assert_called()
