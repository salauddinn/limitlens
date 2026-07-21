#!/usr/bin/env python3
"""Tests for the unified LimitLens runner."""

from unittest.mock import patch

from limitlens.runner import (
    build_command,
    classify_prompt,
    collect_quota_data,
    prepare_prompt_for_tool,
    route_prompt,
)


def test_classify_prompt_routes_common_intents():
    assert classify_prompt("Plan the auth migration") == "planning"
    assert classify_prompt("Fix the failing tests") == "coding"
    assert classify_prompt("Write a bash script for releases") == "cli"
    assert classify_prompt("What should I do next?") == "general"


def test_route_planning_prefers_pi_when_enabled_without_hard_quota():
    decision = route_prompt(
        "Plan the auth migration",
        config={"pi": {"enabled": True}},
        quota_data={},
        require_executable=False,
    )

    assert decision.tool_id == "pi"
    assert decision.task_kind == "planning"
    assert decision.command[0] == "pi"
    assert decision.command[1] == "Plan the auth migration"
    assert "Plan the auth migration" in decision.command[1]


def test_route_coding_uses_antigravity_quota_candidate():
    quota_data = {
        "antigravity": {
            "profiles": [
                {
                    "name": "agy-cli",
                    "source": "cli",
                    "status": "fresh",
                    "models": [{"label": "Gemini Pro", "pct_left": 72.0}],
                }
            ]
        }
    }

    decision = route_prompt(
        "Build the dashboard",
        config={"antigravity": {"enabled": True}},
        quota_data=quota_data,
        require_executable=False,
    )

    assert decision.tool_id == "agy"
    assert "72% left" in decision.reason


def test_prepare_prompt_passes_through_by_default():
    routed = prepare_prompt_for_tool(
        "pi",
        "Research this",
        "planning",
        config={"runner": {"tools": {"pi": {}}}},
    )

    assert routed == "Research this"


def test_prepare_prompt_supports_explicit_template():
    routed = prepare_prompt_for_tool(
        "pi",
        "Research this",
        "planning",
        config={"runner": {"tools": {"pi": {"prompt_template": "[{task_kind}] {prompt}"}}}},
    )

    assert routed == "[planning] Research this"


def test_build_command_supports_configured_stdin_mode():
    command, stdin_text = build_command(
        "pi",
        "Research this",
        config={"runner": {"tools": {"pi": {"command": "pi --quiet", "prompt_mode": "stdin"}}}},
    )

    assert command == ("pi", "--quiet")
    assert stdin_text == "Research this"


def test_build_command_uses_production_cli_defaults():
    assert build_command("pi", "hello") == (("pi", "hello"), None)
    assert build_command("agy", "hello") == (("agy", "--prompt-interactive", "hello"), None)
    assert build_command("amp", "hello") == (("amp",), "hello")
    assert build_command("codex", "hello") == (("codex", "hello"), None)
    assert build_command("opencode", "hello") == (("opencode", "run", "hello"), None)
    assert build_command("commandcode", "hello") == (("cmd", "hello"), None)
    assert build_command("cline", "hello") == (("cline", "hello"), None)
    assert build_command("kilo", "hello") == (("kilo", "run", "hello"), None)


def test_route_can_force_cline_when_enabled_without_quota():
    decision = route_prompt(
        "Build the dashboard",
        config={"cline": {"enabled": True}},
        preferred_tool="cline",
        quota_data={},
        require_executable=False,
    )

    assert decision.tool_id == "cline"
    assert decision.label == "Cline CLI"
    assert decision.reason == "enabled; no hard quota source"


def test_route_can_force_cline_with_default_config():
    decision = route_prompt(
        "Build the dashboard",
        config={},
        preferred_tool="cline",
        quota_data={},
        require_executable=False,
    )

    assert decision.tool_id == "cline"
    assert decision.reason == "enabled; no hard quota source"


def test_build_command_rejects_unknown_prompt_mode():
    try:
        build_command("pi", "hello", config={"runner": {"tools": {"pi": {"prompt_mode": "typo"}}}})
    except ValueError as exc:
        assert "unsupported prompt_mode" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_route_respects_runner_ignored_tools():
    quota_data = {
        "antigravity": {
            "profiles": [
                {
                    "name": "agy-cli",
                    "source": "cli",
                    "status": "fresh",
                    "models": [{"label": "Gemini Pro", "pct_left": 72.0}],
                }
            ]
        },
        "amp": {"tiers": [{"total": 10.0, "remaining": 8.0, "replenish_rate": 0}]},
    }

    decision = route_prompt(
        "Build the dashboard",
        config={"antigravity": {"enabled": True}, "amp": {"enabled": True}, "runner": {"ignored_tools": ["agy"]}},
        quota_data=quota_data,
        require_executable=False,
    )

    assert decision.tool_id == "amp"


def test_route_respects_provider_disabled_even_when_runner_command_configured():
    try:
        route_prompt(
            "Build the dashboard",
            config={"antigravity": {"enabled": False}, "runner": {"tools": {"agy": {"command": "agy"}}}},
            preferred_tool="agy",
            quota_data={"antigravity": {"profiles": []}},
            require_executable=False,
        )
    except RuntimeError as exc:
        assert "disabled by provider config" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_route_forced_unknown_tool_fails():
    try:
        route_prompt("hello", preferred_tool="unknown", quota_data={}, require_executable=False)
    except ValueError as exc:
        assert "unsupported runner tool" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# ── collect_quota_data: Codex auto-refresh ─────────────────────────────────


def _codex_result(stale_accounts, fresh_accounts):
    """Build a two-call codex data fake: first stale, then fresh."""
    calls = {"count": 0}

    def fake_get_codex_data(args, config):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"accounts": stale_accounts}
        return {"accounts": fresh_accounts}

    return calls, fake_get_codex_data


def test_collect_quota_data_refreshes_stale_codex_accounts():
    stale = [{"name": "p1", "limits": [{"label": "weekly", "is_stale": True}]}]
    fresh = [{"name": "p1", "limits": [{"label": "weekly", "is_stale": False}]}]
    calls, fake_codex = _codex_result(stale, fresh)

    refresh_calls = []

    def fake_refresh(names, config=None, timeout=30):
        refresh_calls.append(list(names))
        return {n: {"ok": True, "error": None} for n in names}

    with patch("limitlens.runner.get_codex_data", side_effect=fake_codex), \
         patch("limitlens.runner._refresh_codex_accounts", side_effect=fake_refresh), \
         patch("limitlens.runner.is_provider_enabled", side_effect=lambda c, k, default=False: k == "codex"):
        result = collect_quota_data({})

    assert calls["count"] == 2, "codex should be fetched twice (stale then fresh)"
    assert refresh_calls == [["p1"]]
    assert result["codex"]["accounts"][0]["limits"][0]["is_stale"] is False


def test_collect_quota_data_skips_refresh_when_auto_refresh_disabled():
    stale = [{"name": "p1", "limits": [{"label": "weekly", "is_stale": True}]}]

    def fake_codex(args, config):
        return {"accounts": stale}

    refresh_calls = []

    def fake_refresh(names, config=None, timeout=30):
        refresh_calls.append(list(names))
        return {}

    with patch("limitlens.runner.get_codex_data", side_effect=fake_codex), \
         patch("limitlens.runner._refresh_codex_accounts", side_effect=fake_refresh), \
         patch("limitlens.runner.is_provider_enabled", side_effect=lambda c, k, default=False: k == "codex"):
        result = collect_quota_data({"codex": {"auto_refresh": False}})

    assert refresh_calls == [], "should not refresh when auto_refresh is false"
    assert result["codex"]["accounts"][0]["limits"][0]["is_stale"] is True


def test_collect_quota_data_skips_refresh_when_no_stale_accounts():
    fresh = [{"name": "p1", "limits": [{"label": "weekly", "is_stale": False}]}]

    def fake_codex(args, config):
        return {"accounts": fresh}

    refresh_calls = []

    def fake_refresh(names, config=None, timeout=30):
        refresh_calls.append(list(names))
        return {}

    with patch("limitlens.runner.get_codex_data", side_effect=fake_codex), \
         patch("limitlens.runner._refresh_codex_accounts", side_effect=fake_refresh), \
         patch("limitlens.runner.is_provider_enabled", side_effect=lambda c, k, default=False: k == "codex"):
        collect_quota_data({})

    assert refresh_calls == [], "should not refresh when nothing is stale"


def test_collect_quota_data_skips_refresh_on_codex_error():
    def fake_codex(args, config):
        return {"error": "codex provider failed"}

    refresh_calls = []

    def fake_refresh(names, config=None, timeout=30):
        refresh_calls.append(list(names))
        return {}

    with patch("limitlens.runner.get_codex_data", side_effect=fake_codex), \
         patch("limitlens.runner._refresh_codex_accounts", side_effect=fake_refresh), \
         patch("limitlens.runner.is_provider_enabled", side_effect=lambda c, k, default=False: k == "codex"):
        collect_quota_data({})

    assert refresh_calls == [], "should not refresh when codex returned an error"
