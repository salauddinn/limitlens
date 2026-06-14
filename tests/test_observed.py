import json
import sqlite3
import tempfile
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest
from limitlens.providers.observed import (
    usage_window_start, millis_from_dt, empty_usage_totals, add_usage,
    token_total_value, usage_summary_rows, float_value, first_present,
    normalize_credit_limit, get_opencode_credit_limits, model_is_ignored,
    model_parent_label, get_opencode_usage, find_values_by_key,
    first_number_for_keys, first_string_for_keys, parse_otel_timestamp,
    get_copilot_cli_usage, pi_usage_cost, pi_usage_tokens, get_pi_usage,
    get_opencode_data, get_pi_data, display_pi_text, display_usage_rows,
    display_usage_rows_detailed, format_credit_amount, format_credit_pair,
    display_credit_limits, display_usage_source, display_opencode_text,
    compact_reco_name, display_at_glance
)

# test usage_window_start
def test_usage_window_start():
    dt = usage_window_start(1)
    now = datetime.now(timezone.utc)
    assert (now - dt).total_seconds() >= 86390 # roughly 1 day
    
# test millis_from_dt
def test_millis_from_dt():
    dt = datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert millis_from_dt(dt) == 1672531200000

def test_empty_usage_totals():
    totals = empty_usage_totals()
    assert totals["requests"] == 0
    assert totals["cost"] == 0.0
    assert totals["tokens"]["total"] == 0

def test_add_usage():
    t = empty_usage_totals()
    add_usage(t, cost=1.5, tokens={"total": 10, "input": 4, "output": 6})
    assert t["requests"] == 1
    assert t["cost"] == 1.5
    assert t["tokens"]["total"] == 10
    assert t["tokens"]["input"] == 4
    assert t["tokens"]["output"] == 6
    # bad cost
    add_usage(t, cost="invalid", tokens={"cache": {"read": 5, "write": 5}})
    assert t["requests"] == 2
    assert t["cost"] == 1.5
    assert t["tokens"]["cache_read"] == 5

# test token_total_value
def test_token_total_value():
    assert token_total_value({"total": 10}) == 10
    assert token_total_value({"input": 2, "output": 3}) == 5
    assert token_total_value({"cache": {"read": 4, "write": 1}}) == 5
    assert token_total_value(None) == 0

def test_usage_summary_rows():
    by_key = {
        ("openai", "gpt-4"): {"requests": 10, "cost": 0.5, "tokens": {"total": 100}, "parent": "gpt"},
        ("anthropic", "claude"): {"requests": 5, "cost": 0.1, "tokens": {"total": 50}},
    }
    rows = usage_summary_rows(by_key)
    assert len(rows) == 2
    assert rows[0]["model"] == "gpt-4"
    assert rows[0]["parent"] == "gpt"
    assert rows[1]["model"] == "claude"

def test_float_value():
    assert float_value(1.5) == 1.5
    assert float_value("2.5") == 2.5
    assert float_value("invalid", 3.0) == 3.0

def test_first_present():
    assert first_present({"a": 1, "b": 2}, ("c", "b", "a")) == 2
    assert first_present({}, ("a",)) is None

def test_normalize_credit_limit():
    assert normalize_credit_limit({"credits_total": 100, "credits_remaining": 50})["remaining"] == 50.0
    assert normalize_credit_limit({"total": 100, "used": 20})["remaining"] == 80.0
    assert normalize_credit_limit({"remaining": 40, "used": 60})["total"] == 100.0
    assert normalize_credit_limit(None) is None
    assert normalize_credit_limit({"total": -1, "remaining": -1, "used": -1}) is None
    assert normalize_credit_limit({"total": 100})["remaining"] == 100.0
    assert normalize_credit_limit({"remaining": 100})["total"] == 100.0

def test_get_opencode_credit_limits():
    cfg = {"credit_limits": [{"credits_total": 100, "credits_remaining": 50}]}
    limits = get_opencode_credit_limits(cfg)
    assert len(limits) == 1
    assert limits[0]["total"] == 100
    
    cfg2 = {"credit_limits": {"my_limit": {"credits_total": 10, "credits_remaining": 5}}}
    limits2 = get_opencode_credit_limits(cfg2)
    assert len(limits2) == 1
    assert limits2[0]["name"] == "my_limit"

    cfg3 = {"credits_total": 50, "credits_remaining": 10}
    assert len(get_opencode_credit_limits(cfg3)) == 1
    
    cfg4 = {"credit_limits": {"my_limit": 5, "credits_total": 100, "credits_remaining": 50}}
    assert len(get_opencode_credit_limits(cfg4)) == 1

def test_model_is_ignored():
    assert model_is_ignored("openai", "gpt-4", ["openai/gpt-4"])
    assert model_is_ignored("openai", "gpt-4", "gpt-4")
    assert not model_is_ignored("openai", "gpt-4", ["claude"])
    assert not model_is_ignored("openai", "gpt-4", None)

def test_model_parent_label():
    parents = {"openai/gpt-4": "gpt4-group", "anthropic/*": "anthropic-group", "llama": "meta"}
    assert model_parent_label("openai", "gpt-4", parents) == "gpt4-group"
    assert model_parent_label("anthropic", "claude-3", parents) == "anthropic-group"
    assert model_parent_label("meta", "llama", parents) == "meta"
    assert model_parent_label("meta", "llama2", parents) is None
    assert model_parent_label("a", "b", None) is None

def test_find_values_by_key():
    assert find_values_by_key({"a": 1, "b": {"a": 2}}, {"a"}) == [1, 2]
    assert find_values_by_key([{"a": 1}, {"a": 2}], {"a"}) == [1, 2]

def test_first_number_for_keys():
    assert first_number_for_keys({"a": 1, "b": "invalid", "c": {"a": 2}}, ["a"]) == 1
    assert first_number_for_keys({"x": "y"}, ["a"]) == 0

def test_first_string_for_keys():
    assert first_string_for_keys({"a": "val1"}, ["a"]) == "val1"
    assert first_string_for_keys({"a": ""}, ["a"]) == "unknown"

def test_parse_otel_timestamp():
    assert parse_otel_timestamp(1672531200.0) == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert parse_otel_timestamp(1672531200000) == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert parse_otel_timestamp(1672531200000000000) == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert parse_otel_timestamp("1672531200") == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert parse_otel_timestamp("2023-01-01T00:00:00Z") == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert parse_otel_timestamp(None) is None
    assert parse_otel_timestamp("invalid") is None

@pytest.fixture
def opencode_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE message (id INTEGER, time_created INTEGER, data TEXT)")
    
    # insert data
    now_ms = millis_from_dt(datetime.now(timezone.utc))
    valid_data = {
        "role": "assistant",
        "providerID": "openai",
        "modelID": "gpt-4",
        "tokens": {"total": 100},
        "cost": 0.05,
        "time": {"created": now_ms}
    }
    conn.execute("INSERT INTO message VALUES (1, ?, ?)", (now_ms, json.dumps(valid_data)))
    
    invalid_data = "{"
    conn.execute("INSERT INTO message VALUES (2, ?, ?)", (now_ms, invalid_data))
    
    no_tokens = {"role": "assistant"}
    conn.execute("INSERT INTO message VALUES (3, ?, ?)", (now_ms, json.dumps(no_tokens)))
    
    conn.commit()
    conn.close()
    
    yield path
    os.remove(path)

def test_get_opencode_usage(opencode_db):
    config = {
        "opencode": {
            "enabled": True,
            "db_path": opencode_db,
            "providers": ["openai"],
            "ignored_models": ["gpt-3.5"],
            "parents": {"openai/gpt-4": "gpt4-parent"}
        }
    }
    usage = get_opencode_usage(config)
    assert "error" not in usage
    assert "windows" in usage
    models = usage["windows"][0]["models"]
    assert len(models) == 1
    assert models[0]["model"] == "gpt-4"
    assert models[0]["parent"] == "gpt4-parent"
    assert models[0]["tokens"]["total"] == 100
    
    # disabled
    assert get_opencode_usage({"opencode": {"enabled": False}}) == {"disabled": True}
    # missing db
    assert "error" in get_opencode_usage({"opencode": {"db_path": "/nonexistent/db.sqlite"}})

def test_get_opencode_usage_with_reset(opencode_db):
    config = {
        "opencode": {
            "enabled": True,
            "db_path": opencode_db,
            "providers": ["openai"]
        }
    }
    
    future_reset = datetime.now(timezone.utc) + timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=future_reset):
        usage = get_opencode_usage(config)
        assert len(usage["windows"][0]["models"]) == 0
        
    past_reset = datetime.now(timezone.utc) - timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=past_reset):
        usage = get_opencode_usage(config)
        assert len(usage["windows"][0]["models"]) == 1

@pytest.fixture
def copilot_otel():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    valid_record = {
        "timeUnixNano": now_ns,
        "gen_ai.usage.input_tokens": 50,
        "gen_ai.usage.output_tokens": 100,
        "gen_ai.system": "github-copilot",
        "gen_ai.request.model": "gpt-4-copilot"
    }
    
    with open(path, "w") as f:
        f.write(json.dumps(valid_record) + "\n")
        f.write("invalid json\n")
        f.write('{"timeUnixNano": 1}\n') # old
        
    yield path
    os.remove(path)

def test_get_copilot_cli_usage(copilot_otel):
    config = {
        "copilot_cli": {
            "enabled": True,
            "otel_jsonl_path": copilot_otel
        }
    }
    usage = get_copilot_cli_usage(config)
    assert "error" not in usage
    assert len(usage["windows"]) > 0
    models = usage["windows"][0]["models"]
    if models:
        assert models[0]["model"] == "gpt-4-copilot"
        assert models[0]["tokens"]["input"] == 50
        assert models[0]["tokens"]["output"] == 100
        
    # test reset cutoff logic
    future_reset = datetime.now(timezone.utc) + timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=future_reset):
        usage_reset = get_copilot_cli_usage(config)
        assert len(usage_reset["windows"][0]["models"]) == 0

    past_reset = datetime.now(timezone.utc) - timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=past_reset):
        usage_past = get_copilot_cli_usage(config)
        assert len(usage_past["windows"][0]["models"]) > 0

    assert get_copilot_cli_usage({"copilot_cli": {"enabled": False}}) == {"disabled": True}
    assert "error" in get_copilot_cli_usage({"copilot_cli": {"otel_jsonl_path": "/nonexistent/path"}})

def test_pi_usage_cost():
    assert pi_usage_cost({"cost": 1.5}) == 1.5
    assert pi_usage_cost({"cost": {"total": 2.0}}) == 2.0
    assert pi_usage_cost(None) == 0

def test_pi_usage_tokens():
    assert pi_usage_tokens({"input": 10, "output": 20})["total"] == 30
    assert pi_usage_tokens({"totalTokens": 100})["total"] == 100
    assert pi_usage_tokens(None) == {}

@pytest.fixture
def pi_sessions():
    path = tempfile.mkdtemp()
    
    now_ms = millis_from_dt(datetime.now(timezone.utc))
    valid_record = {
        "type": "message",
        "timestamp": now_ms,
        "message": {
            "role": "assistant",
            "provider": "anthropic",
            "model": "claude-3",
            "usage": {"input": 100, "output": 50, "cost": 0.01}
        }
    }
    
    with open(os.path.join(path, "session.jsonl"), "w") as f:
        f.write(json.dumps(valid_record) + "\n")
        f.write("invalid json\n")
        
    yield path
    # clean up
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))
    os.rmdir(path)

def test_get_pi_usage(pi_sessions):
    config = {
        "pi": {
            "enabled": True,
            "sessions_dir": pi_sessions
        }
    }
    usage = get_pi_usage(config)
    assert "error" not in usage
    models = usage["windows"][0]["models"]
    if models:
        assert models[0]["model"] == "claude-3"
        assert models[0]["tokens"]["input"] == 100

    # test reset cutoff logic
    future_reset = datetime.now(timezone.utc) + timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=future_reset):
        usage_reset = get_pi_usage(config)
        assert len(usage_reset["windows"][0]["models"]) == 0

    past_reset = datetime.now(timezone.utc) - timedelta(days=1)
    with patch("limitlens.providers.observed.get_spend_reset_time", return_value=past_reset):
        usage_past = get_pi_usage(config)
        assert len(usage_past["windows"][0]["models"]) > 0

    assert get_pi_usage({"pi": {"enabled": False}}) == {"disabled": True}
    assert "error" in get_pi_usage({"pi": {"sessions_dir": "/nonexistent/pi/dir"}})

def test_get_opencode_data():
    data = get_opencode_data({}, {})
    assert "opencode" in data
    assert "pi" in data
    assert "copilot_cli" in data
    assert "claude" in data

def test_get_pi_data():
    from limitlens.providers.observed import get_pi_data
    # just tests the proxy function
    assert "error" in get_pi_data({}, {"pi": {"sessions_dir": "/non/ex"}})

def test_claude_usage_tokens():
    from limitlens.providers.observed import claude_usage_tokens
    assert claude_usage_tokens({}) == {"total": 0, "input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    res = claude_usage_tokens({"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100})
    assert res["total"] == 115
    assert res["cache_write"] == 100

@pytest.fixture
def claude_sessions():
    path = tempfile.mkdtemp()
    
    # Claude Code has timestamp at root level usually, but we fall back to both.
    now_ms = millis_from_dt(datetime.now(timezone.utc))
    valid_record = {
        "timestamp": now_ms,
        "message": {
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "usage": {"input_tokens": 1000, "output_tokens": 500}
        }
    }
    
    with open(os.path.join(path, "session.jsonl"), "w") as f:
        f.write(json.dumps(valid_record) + "\n")
        f.write("invalid json\n")
        
    yield path
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))
    os.rmdir(path)

def test_get_claude_usage(claude_sessions):
    from limitlens.providers.observed import get_claude_usage
    config = {
        "claude": {
            "enabled": True,
            "sessions_dir": claude_sessions
        }
    }
    usage = get_claude_usage(config)
    assert "error" not in usage
    models = usage["windows"][0]["models"]
    if models:
        assert models[0]["model"] == "claude-3-5-sonnet-20241022"
        assert models[0]["tokens"]["input"] == 1000

    assert get_claude_usage({"claude": {"enabled": False}}) == {"disabled": True}
    assert "error" in get_claude_usage({"claude": {"sessions_dir": "/nonexistent/claude/dir"}})

# Output tests
class DummyArgs:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

def test_display_usage_rows(capsys):
    rows = [{"provider": "p1", "model": "m1", "requests": 1, "cost": 1.0, "tokens": {"total": 100}, "parent": "p"}]
    display_usage_rows(rows, DummyArgs(verbose=False, no_color=True))
    captured = capsys.readouterr()
    assert "m1" in captured.out
    
    display_usage_rows([], DummyArgs(verbose=False, no_color=True))
    assert "no usage" in capsys.readouterr().out

def test_display_usage_rows_detailed(capsys):
    rows = [{"provider": "p1", "model": "m1", "requests": 1, "cost": 1.0, "tokens": {"total": 100, "input": 50, "output": 50}, "parent": "p"}]
    display_usage_rows_detailed(rows, DummyArgs(verbose=False, no_color=True))
    captured = capsys.readouterr()
    assert "m1" in captured.out

    display_usage_rows_detailed([], DummyArgs(verbose=False, no_color=True))
    assert "no usage" in capsys.readouterr().out

def test_format_credit_amount():
    assert format_credit_amount(10.5, "USD") == "$10.50"
    assert format_credit_amount(10.5, "INR") == "₹10.50"
    assert format_credit_amount(10.5, "credits") == "10.50 credits"

def test_format_credit_pair():
    assert format_credit_pair(5.5, 10.0, "USD") == "$5.50/$10.00"
    assert format_credit_pair(5.5, 10.0, "credits") == "5.50/10.00 credits"

def test_display_credit_limits(capsys):
    limits = [{"name": "test", "remaining": 5.0, "total": 10.0, "used": 5.0, "pct_left": 50.0, "unit": "USD"}]
    display_credit_limits(limits, DummyArgs(no_color=True))
    captured = capsys.readouterr()
    assert "test" in captured.out
    assert "$5.00/$10.00" in captured.out

def test_display_usage_source(capsys):
    display_usage_source("test", {"disabled": True}, DummyArgs())
    assert capsys.readouterr().out == ""
    
    display_usage_source("test", {"error": "err"}, DummyArgs(no_color=True, verbose=True))
    assert "err" in capsys.readouterr().out
    
    data = {
        "windows": [{"days": 1, "since": "2023-01-01", "models": [{"provider": "p1", "model": "m1", "requests": 1, "cost": 1.0, "tokens": {"total": 100}, "parent": "p"}]}]
    }
    display_usage_source("test", data, DummyArgs(no_color=True, verbose=True))
    assert "m1" in capsys.readouterr().out
    
    data_empty = {"windows": [{"days": 1, "since": "2023-01-01", "models": []}]}
    display_usage_source("test", data_empty, DummyArgs(no_color=True, verbose=False, all=False))
    assert capsys.readouterr().out == ""
    
    display_usage_source("test", data_empty, DummyArgs(no_color=True, verbose=True, all=False))
    assert "no usage" in capsys.readouterr().out

def test_display_opencode_text(capsys):
    data = {
        "opencode": {"windows": [{"days": 1, "since": "2023-01-01", "models": [{"provider": "p1", "model": "m1", "requests": 1, "cost": 1.0, "tokens": {"total": 100}, "parent": "p"}]}]}
    }
    display_opencode_text(data, DummyArgs(tool="all", no_color=True, verbose=True))
    assert "m1" in capsys.readouterr().out
    
    data_empty = {"opencode": {"windows": []}}
    display_opencode_text(data_empty, DummyArgs(tool="all", no_color=True, verbose=False, all=False))
    assert capsys.readouterr().out == ""

def test_display_pi_text(capsys):
    data = {"windows": [{"days": 1, "since": "2023-01-01", "models": [{"provider": "p1", "model": "m1", "requests": 1, "cost": 1.0, "tokens": {"total": 100}, "parent": "p"}]}]}
    display_pi_text(data, DummyArgs(tool="all", no_color=True, verbose=True))
    assert "m1" in capsys.readouterr().out

def test_compact_reco_name():
    assert compact_reco_name("antigravity: Claude Opus 4.6") == "ag: Opus 4.6"
    assert compact_reco_name("Gemini 3.5 Flash") == "Flash"
    assert compact_reco_name("Gemini 3.1 Pro") == "Gemini Pro"

def test_display_at_glance(capsys):
    result = {
        "opencode": {
            "op": {
                "windows": [{"days": 1, "models": [{"provider": "p", "model": "m", "tokens": {"total": 100}, "cost": 0.0, "parent": ""}]}]
            }
        }
    }
    recs = {
        "hard": [{"name": "Opus", "reset_label": "reset", "headroom_pct": 100.0}],
    }
    display_at_glance(result, recs, DummyArgs(no_color=True))
    captured = capsys.readouterr()
    assert "hard task" in captured.out
    assert "Opus" in captured.out
    assert "p/m" in captured.out
    
    display_at_glance({}, {}, DummyArgs(no_color=True))
    assert "no usable option" in capsys.readouterr().out
