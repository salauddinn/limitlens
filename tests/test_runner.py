#!/usr/bin/env python3
"""Tests for the unified LimitLens runner."""

from limitlens.runner import build_command, classify_prompt, prepare_prompt_for_tool, route_prompt


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
