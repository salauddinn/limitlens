import json
import os
import urllib.error
from unittest.mock import patch, MagicMock, ANY


from limitlens.providers.pioneer import (
    _float,
    _has_config_balance,
    _pioneer_money,
    parse_pioneer_billing,
    get_pioneer_data,
    display_pioneer_text,
)


class DummyArgs:
    def __init__(self, **kwargs):
        self.redact = True
        self.verbose = False
        self.all = False
        self.no_color = False
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_float():
    assert _float("1.23") == 1.23
    assert _float(1.23) == 1.23
    assert _float(None, 2.0) == 2.0
    assert _float("invalid", 3.0) == 3.0


def test_has_config_balance():
    assert _has_config_balance({"tiers": []}) is True
    assert _has_config_balance({"credits_remaining": 10}) is True
    assert _has_config_balance({"other": 10}) is False
    assert _has_config_balance(None) is False
    assert _has_config_balance("string") is False


def test_pioneer_money():
    assert _pioneer_money("1234") == 12.34
    assert _pioneer_money(0) == 0.0
    assert _pioneer_money(None) == 0.0


@patch("limitlens.providers.pioneer.redact_email")
@patch("limitlens.providers.pioneer.load_display_config")
def test_parse_pioneer_billing(mock_disp, mock_redact):
    mock_disp.return_value = {"auto_hide_enabled": True}
    mock_redact.return_value = "te**@example.com"

    assert parse_pioneer_billing(None, DummyArgs()) == {"error": "Unexpected response format"}

    data = {
        "email": "test@example.com",
        "team_id": "team_1",
        "team_name": "Team",
        "plan": "Pro",
        "used_today": "100.5",
        "inferences_today": "10",
        "unit": "credits",
        "tiers": [
            {
                "label": "Credits",
                "remaining": 50,
                "total": 100,
                "used": 50,
                "unit": "credits"
            },
            {
                "label": "Empty",
                "remaining": 0,
                "total": 0,
                "used": 0
            },
            {
                "label": "NoTotal",
                "remaining": 10,
                "total": 0,
                "used": 0
            },
            {
                "label": "NoUsed",
                "remaining": 50,
                "total": 100,
                "used": 0
            }
        ]
    }

    args = DummyArgs()
    res = parse_pioneer_billing(data, args)

    assert res["email"] == "te**@example.com"
    assert res["team_id"] == "team_1"
    assert res["team_name"] == "Team"
    assert res["plan"] == "Pro"
    assert res["used_today"] == 100.5
    assert res["inferences_today"] == 10

    assert len(res["tiers"]) == 3  # Empty tier should be skipped

    t1 = res["tiers"][0]
    assert t1["remaining"] == 50.0
    assert t1["total"] == 100.0
    assert t1["used"] == 50.0
    assert t1["pct_left"] == 50.0
    assert t1["pct_used"] == 50.0
    assert t1["visible"] is True

    t2 = res["tiers"][1]
    assert t2["remaining"] == 10.0
    assert t2["total"] is None
    assert t2["used"] == 0.0
    assert t2["pct_left"] is None

    t3 = res["tiers"][2]
    assert t3["remaining"] == 50.0
    assert t3["total"] == 100.0
    assert t3["used"] == 50.0  # max(0, total - remaining)

    # Test auto hide visible false
    data["tiers"] = [{"label": "Hidden", "remaining": 5, "total": 100, "used": 95}]
    res = parse_pioneer_billing(data, DummyArgs())
    assert res["tiers"][0]["visible"] is False


@patch("limitlens.providers.pioneer.load_display_config")
def test_parse_pioneer_billing_no_tiers_but_credit_limit(mock_disp):
    mock_disp.return_value = {"auto_hide_enabled": False}
    data = {
        "credit_limit": 1000,
        "free_tier_remaining": 500,
        "total_usage": 500
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert len(res["tiers"]) == 1
    t = res["tiers"][0]
    assert t["total"] == 10.0
    assert t["remaining"] == 5.0
    assert t["used"] == 5.0

    data = {
        "credit_limit": 0,
        "free_tier_remaining": 500,
        "total_usage": 500
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert res["tiers"][0]["total"] == 10.0

    data = {
        "credit_limit": 1000,
        "free_tier_remaining": 0,
        "total_usage": 500
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert res["tiers"][0]["remaining"] == 5.0

    data = {
        "credit_limit": 1000,
        "free_tier_remaining": 1000,
        "total_usage": 0
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert res["tiers"][0]["used"] == 0.0


@patch("limitlens.providers.pioneer.load_display_config")
def test_parse_pioneer_billing_no_tiers_but_credits_remaining(mock_disp):
    mock_disp.return_value = {"auto_hide_enabled": False}
    data = {
        "credits_total": 100.0,
        "credits_remaining": 20.0
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert len(res["tiers"]) == 1
    t = res["tiers"][0]
    assert t["total"] == 100.0
    assert t["remaining"] == 20.0
    assert t["used"] == 80.0

    data = {
        "credits_total": 0.0,
        "credits_remaining": 20.0
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert len(res["tiers"]) == 1
    t = res["tiers"][0]
    assert t["total"] == 20.0
    assert t["used"] == 0.0

    data = {
        "credits_total": 100.0,
        "credits_remaining": 20.0,
        "credits_used": 50.0
    }
    res = parse_pioneer_billing(data, DummyArgs())
    assert len(res["tiers"]) == 1
    t = res["tiers"][0]
    assert t["used"] == 50.0


@patch("limitlens.providers.pioneer.load_limitlens_config")
@patch.dict(os.environ, clear=True)
@patch("limitlens.keychain.get_keychain_token", return_value=None)
def test_get_pioneer_data_no_token(mock_keychain, mock_cfg):
    mock_cfg.return_value = {}
    res = get_pioneer_data(DummyArgs())
    assert "PIONEER_API_TOKEN not set" in res["error"]
    assert "limitlens auth pioneer" in res["error"]

    mock_cfg.return_value = {"pioneer": {"tiers": [{"label": "test", "remaining": 10, "total": 100, "used": 90}]}}
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        assert "tiers" in res
        assert len(res["tiers"]) == 1


@patch("limitlens.providers.pioneer.load_limitlens_config")
@patch("limitlens.providers.pioneer._open_no_redirect")
@patch.dict(os.environ, {"PIONEER_API_TOKEN": "test_token"}, clear=True)
@patch("limitlens.keychain.get_keychain_token", return_value=None)
def test_get_pioneer_data_success(mock_keychain, mock_open, mock_cfg):
    mock_cfg.return_value = {}
    cm = MagicMock()
    cm.read.return_value = json.dumps({"data": {"email": "test@pioneer"}}).encode("utf-8")
    mock_open.return_value.__enter__.return_value = cm

    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs(redact=False))
        assert res["email"] == "test@pioneer"

    # Test nested empty data
    cm.read.return_value = json.dumps({"data": {}}).encode("utf-8")
    res = get_pioneer_data(DummyArgs())
    assert res["error"] == "Empty data in Pioneer API response"

    # Test completely empty response
    cm.read.return_value = json.dumps(None).encode("utf-8")
    res = get_pioneer_data(DummyArgs())
    assert res["error"] == "Empty response from Pioneer API"

    # Test invalid json
    cm.read.return_value = b"invalid json"
    res = get_pioneer_data(DummyArgs())
    assert res["error"] == "Invalid JSON response from Pioneer API"


@patch("limitlens.providers.pioneer.load_limitlens_config")
@patch("limitlens.providers.pioneer._open_no_redirect")
@patch.dict(os.environ, {"PIONEER_API_TOKEN": "test_token", "PIONEER_TEAM_ID": "team_1"}, clear=True)
@patch("limitlens.keychain.get_keychain_token", return_value=None)
def test_get_pioneer_data_with_team(mock_keychain, mock_open, mock_cfg):
    mock_cfg.return_value = {}
    cm = MagicMock()
    cm.read.return_value = json.dumps({"data": {"email": "test@pioneer"}}).encode("utf-8")
    mock_open.return_value.__enter__.return_value = cm

    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs(redact=False))
        assert res["email"] == "test@pioneer"


@patch("limitlens.providers.pioneer.load_limitlens_config")
@patch("limitlens.providers.pioneer._open_no_redirect")
@patch.dict(os.environ, {"PIONEER_API_TOKEN": "test_token"}, clear=True)
@patch("limitlens.keychain.get_keychain_token", return_value=None)
def test_get_pioneer_data_http_error(mock_keychain, mock_open, mock_cfg):
    mock_cfg.return_value = {}
    mock_open.side_effect = urllib.error.URLError("test error")
    res = get_pioneer_data(DummyArgs())
    assert "API request failed" in res["error"]

    mock_open.side_effect = Exception("general error")
    res = get_pioneer_data(DummyArgs())
    assert "Request failed" in res["error"]


@patch("limitlens.providers.pioneer.load_limitlens_config")
@patch("limitlens.providers.pioneer._open_no_redirect")
@patch.dict(os.environ, {"PIONEER_API_TOKEN": "test_token"}, clear=True)
def test_get_pioneer_data_fallback_config(mock_open, mock_cfg):
    mock_cfg.return_value = {"pioneer": {"tiers": [{"label": "fallback", "remaining": 10, "total": 100, "used": 90}]}}

    mock_open.side_effect = urllib.error.URLError("error")
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        assert res["tiers"][0]["label"] == "fallback"

    mock_open.side_effect = Exception("error")
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        assert res["tiers"][0]["label"] == "fallback"

    cm = MagicMock()
    cm.read.return_value = json.dumps(None).encode("utf-8")
    mock_open.return_value.__enter__.return_value = cm
    mock_open.side_effect = None
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        assert res["tiers"][0]["label"] == "fallback"

    cm.read.return_value = json.dumps({"data": None}).encode("utf-8")
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        assert res["tiers"][0]["label"] == "fallback"

    cm.read.return_value = json.dumps({"data": {"email": "test@pioneer"}}).encode("utf-8")
    with patch("limitlens.providers.pioneer.load_display_config", return_value={"auto_hide_enabled": False}):
        res = get_pioneer_data(DummyArgs())
        # Merged data test
        assert res["tiers"][0]["label"] == "fallback"


@patch("limitlens.providers.pioneer.print_error")
@patch("limitlens.providers.pioneer.section")
def test_display_pioneer_text_error(mock_section, mock_print_error):
    display_pioneer_text({"error": "test error"}, DummyArgs())
    mock_section.assert_called_with("Pioneer", ANY)
    mock_print_error.assert_called_with("test error", ANY)


@patch("limitlens.providers.pioneer.print_c")
@patch("limitlens.providers.pioneer.identity_line")
@patch("limitlens.providers.pioneer.section")
@patch("builtins.print")
def test_display_pioneer_text(mock_print, mock_section, mock_identity_line, mock_print_c):
    data = {
        "email": "test@pioneer",
        "tiers": [
            {
                "label": "Prefix:LongLabelThatExceedsFifteenCharacters",
                "visible": True,
                "pct_left": 10.0,
                "pct_used": 90.0,
                "total": 100.0,
                "remaining": 10.0,
                "used": 90.0,
                "unit": "tokens"
            },
            {
                "label": "Credits",
                "visible": True,
                "pct_left": 50.0,
                "pct_used": 50.0,
                "total": 100.0,
                "remaining": 50.0,
                "used": 50.0,
                "unit": "$"
            },
            {
                "label": "Hidden",
                "visible": False,
                "pct_left": 50.0,
                "pct_used": 50.0,
                "total": 100.0,
                "remaining": 50.0,
                "used": 50.0,
                "unit": "$"
            }
        ],
        "used_today": 10.5,
        "inferences_today": 5,
    }

    # default color
    args = DummyArgs(verbose=False, all=False, no_color=False)
    display_pioneer_text(data, args)

    # no color
    args = DummyArgs(verbose=False, all=False, no_color=True)
    display_pioneer_text(data, args)

    # empty tiers but verbose
    data_empty = {"email": "test@pioneer", "tiers": []}
    args_verbose = DummyArgs(verbose=True, all=False, no_color=False)
    display_pioneer_text(data_empty, args_verbose)

    # only used today
    data_used = {"email": "test@pioneer", "tiers": [{"label": "C", "visible": True, "pct_left": 0, "pct_used": 0, "total": None, "remaining": 0, "used": 0, "unit": "$"}], "used_today": 10.5, "inferences_today": 0}
    args_norm = DummyArgs(verbose=False, all=False, no_color=False)
    display_pioneer_text(data_used, args_norm)

    # only inferences
    data_inf = {"email": "test@pioneer", "tiers": [{"label": "C", "visible": True, "pct_left": None, "pct_used": None, "total": None, "remaining": 0, "used": 0, "unit": "$"}], "used_today": 0, "inferences_today": 5}
    display_pioneer_text(data_inf, args_norm)

    # not visible, not verbose/all
    display_pioneer_text({"tiers": [{"visible": False}]}, DummyArgs())
