"""Quota-aware launcher for ``limitlens run``.

This module chooses an AI CLI from the user's prompt and the quota data
LimitLens already knows how to collect, then launches that CLI directly.
Routing stays deterministic and configurable so it works without a specific IDE
or local profile.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import is_provider_enabled
from .core import fmt_reset, parse_to_utc, print_c
from .providers import (
    get_amp_data,
    get_antigravity_data,
    get_codex_data,
    get_commandcode_data,
    get_custom_data,
)
from .recommendations import compute_recommendations

from .logging import get_logger
log = get_logger("limitlens.runner")


TaskKind = str


@dataclass(frozen=True)
class ToolSpec:
    """Execution metadata for a supported agent CLI."""

    tool_id: str
    label: str
    provider_key: Optional[str]
    default_command: Tuple[str, ...]
    aliases: Tuple[str, ...] = ()
    quota_optional: bool = False


@dataclass(frozen=True)
class RouteDecision:
    """Result of routing a prompt to an agent."""

    tool_id: str
    label: str
    task_kind: TaskKind
    reason: str
    fallback_chain: Tuple[str, ...]
    command: Tuple[str, ...]
    routed_prompt: str
    stdin_text: Optional[str] = None


TOOL_SPECS: Dict[str, ToolSpec] = {
    "pi": ToolSpec("pi", "Pi", "pi", ("pi",), quota_optional=True),
    "kilo": ToolSpec("kilo", "Kilo Code", "kilo", ("kilo", "run"), quota_optional=True),
    "agy": ToolSpec("agy", "Antigravity CLI", "antigravity", ("agy", "--prompt-interactive"), aliases=("antigravity",)),
    "amp": ToolSpec("amp", "Amp", "amp", ("amp",)),
    "codex": ToolSpec("codex", "Codex", "codex", ("codex",)),
    "opencode": ToolSpec("opencode", "OpenCode", "opencode", ("opencode", "run"), quota_optional=True),
    "cline": ToolSpec("cline", "Cline CLI", "cline", ("cline",), quota_optional=True),
    "commandcode": ToolSpec("commandcode", "Command Code", "commandcode", ("cmd",), aliases=("cmd",)),
}

ALIAS_TO_TOOL = {
    alias: tool_id
    for tool_id, spec in TOOL_SPECS.items()
    for alias in (tool_id, *spec.aliases)
}

PLAN_KEYWORDS = {
    "plan",
    "research",
    "design",
    "investigate",
    "analyze",
    "analyse",
    "review",
    "spec",
    "architecture",
    "architect",
    "adr",
    "brainstorm",
    "strategy",
}
CODE_KEYWORDS = {
    "build",
    "implement",
    "fix",
    "refactor",
    "code",
    "edit",
    "update",
    "add",
    "create",
    "test",
    "debug",
    "wire",
    "migrate",
}
CLI_KEYWORDS = {
    "cli",
    "script",
    "shell",
    "bash",
    "terminal",
    "command",
    "automation",
    "automate",
}

ROUTE_PREFERENCES: Dict[TaskKind, Tuple[str, ...]] = {
    "planning": ("pi", "amp", "codex", "commandcode", "agy", "opencode", "cline", "kilo"),
    "coding": ("agy", "amp", "codex", "commandcode", "kilo", "opencode", "cline", "pi"),
    "cli": ("amp", "codex", "opencode", "commandcode", "cline", "kilo", "pi", "agy"),
    "general": ("pi", "amp", "agy", "codex", "commandcode", "opencode", "cline", "kilo"),
}

RECOMMENDATION_TOOL_ALIASES = {
    "agy": "antigravity",
    "commandcode": "commandcode",
    "pi": "pi",
    "kilo": "kilo",
    "amp": "amp",
    "codex": "codex",
    "opencode": "opencode",
}

def normalize_tool_id(tool: Optional[str]) -> Optional[str]:
    """Return the canonical runner tool id for a user/config value."""

    if not tool:
        return None
    normalized = tool.strip().lower().replace("_", "-")
    normalized = normalized.replace("command-code", "commandcode")
    return ALIAS_TO_TOOL.get(normalized, normalized)


def classify_prompt(prompt: str) -> TaskKind:
    """Classify a natural-language task with fast local heuristics."""

    text = prompt.lower()
    words = set("".join(ch if ch.isalnum() else " " for ch in text).split())
    if words & CLI_KEYWORDS:
        return "cli"
    if words & CODE_KEYWORDS:
        return "coding"
    if words & PLAN_KEYWORDS:
        return "planning"
    return "general"


def _runner_config(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return (config or {}).get("runner") or {}


def _runner_tool_config(config: Optional[Mapping[str, Any]], tool_id: str) -> Mapping[str, Any]:
    tools = _runner_config(config).get("tools") or {}
    return tools.get(tool_id) or {}


def _bool_config(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off")
    return bool(value)


def prepare_prompt_for_tool(tool_id: str, prompt: str, task_kind: TaskKind, config: Optional[Mapping[str, Any]] = None) -> str:
    """Return the prompt sent to a backend after runner-level adaptation."""

    canonical = normalize_tool_id(tool_id) or tool_id
    tool_cfg = _runner_tool_config(config, canonical)
    template = tool_cfg.get("prompt_template")
    if not template:
        return prompt

    return str(template).replace("{prompt}", prompt).replace("{task_kind}", task_kind)


def _coerce_command(value: Any, default: Sequence[str]) -> Tuple[str, ...]:
    if not value:
        return tuple(default)
    if isinstance(value, str):
        parts = list(shlex.split(value))
    elif isinstance(value, (list, tuple)):
        parts = [str(part) for part in value]
    else:
        return tuple(default)
        
    if parts and "=" in parts[0] and not parts[0].startswith("/"):
        parts.insert(0, "env")
    return tuple(parts)


def _format_arg(template: Any, prompt: str) -> str:
    return str(template).replace("{prompt}", prompt)


def build_command(tool_id: str, prompt: str, config: Optional[Mapping[str, Any]] = None, candidate_command: Optional[str] = None) -> Tuple[Tuple[str, ...], Optional[str]]:
    """Build argv/stdin for a tool.

    Configuration is intentionally simple and portable::

        {
          "runner": {
            "tools": {
              "agy": {"command": ["agy"], "prompt_mode": "arg"},
              "pi": {"command": "pi", "args": ["--prompt", "{prompt}"]}
            }
          }
        }

    ``prompt_mode`` may be ``arg`` (default), ``stdin``, or ``none``.  ``args``
    overrides the prompt mode and supports ``{prompt}`` replacement.
    """

    canonical = normalize_tool_id(tool_id) or tool_id
    spec = TOOL_SPECS[canonical]
    tool_cfg = _runner_tool_config(config, canonical)
    
    base_cmd = tool_cfg.get("command")
    if not base_cmd and candidate_command:
        base_cmd = candidate_command
        
    argv = list(_coerce_command(base_cmd, spec.default_command))
    stdin_text: Optional[str] = None

    if "args" in tool_cfg:
        extra = tool_cfg.get("args") or []
        if isinstance(extra, str):
            extra = shlex.split(extra)
        argv.extend(_format_arg(item, prompt) for item in extra)
        return tuple(argv), stdin_text

    default_prompt_mode = "stdin" if canonical == "amp" else "arg"
    prompt_mode = str(tool_cfg.get("prompt_mode") or default_prompt_mode).lower()
    if prompt_mode == "stdin":
        stdin_text = prompt
    elif prompt_mode == "none":
        pass
    elif prompt_mode == "arg":
        argv.append(prompt)
    else:
        raise ValueError(f"unsupported prompt_mode for {canonical}: {prompt_mode}")
    return tuple(argv), stdin_text


def _executable_exists(command: Sequence[str]) -> bool:
    if not command:
        return False
    
    executable = None
    for part in command:
        if part in ("env", "/usr/bin/env"):
            continue
        if "=" in part:
            continue
        executable = part
        break
        
    if not executable:
        return False
    return shutil.which(executable) is not None


def _provider_args() -> argparse.Namespace:
    return argparse.Namespace(
        tool="all",
        all=False,
        verbose=False,
        debug=False,
        json=True,
        no_color=True,
        redact=True,
        days=7,
    )


def collect_quota_data(config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Collect the quota providers relevant to runner routing.

    Provider failures are isolated: a broken quota source should not prevent the
    runner from falling back to another installed CLI.
    """

    config = config or {}
    args = _provider_args()
    fetchers = []
    if is_provider_enabled(config, "codex", default=True):
        fetchers.append(("codex", lambda: get_codex_data(args, config)))
    if is_provider_enabled(config, "amp", default=True):
        fetchers.append(("amp", lambda: get_amp_data(args)))
    if is_provider_enabled(config, "antigravity", default=True):
        fetchers.append(("antigravity", lambda: get_antigravity_data(args, config)))
    if is_provider_enabled(config, "commandcode", default=False):
        fetchers.append(("commandcode", lambda: get_commandcode_data(args, config)))
    if is_provider_enabled(config, "custom_tools", default=False):
        fetchers.append(("custom", lambda: get_custom_data(args, config)))

    result: Dict[str, Any] = {}
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(fetchers))) as executor:
        future_to_key = {executor.submit(fetch): key for key, fetch in fetchers}
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            try:
                result[key] = future.result()
            except Exception as exc:  # pragma: no cover - exact provider failures vary by machine
                log.exception(f"{key} provider failed")
                result[key] = {"error": f"{key} provider failed: {type(exc).__name__}: {exc}"}
    return result


def _candidate_tools(quota_data: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    recs = compute_recommendations(dict(quota_data), parse_to_utc, fmt_reset)
    candidates: Dict[str, Mapping[str, Any]] = {}
    for candidate in recs.get("all_candidates") or []:
        rec_tool = candidate.get("tool")
        for runner_tool, candidate_tool in RECOMMENDATION_TOOL_ALIASES.items():
            if rec_tool == candidate_tool and runner_tool not in candidates:
                candidates[runner_tool] = candidate
    return candidates


def _tool_provider_enabled(tool_id: str, config: Optional[Mapping[str, Any]]) -> bool:
    spec = TOOL_SPECS[tool_id]
    if not spec.provider_key:
        return True
    defaults = {"pi": False, "opencode": True, "cline": True}.get(tool_id, False)
    return is_provider_enabled(config or {}, spec.provider_key, default=defaults)


def _is_enabled_without_quota(tool_id: str, config: Optional[Mapping[str, Any]]) -> bool:
    spec = TOOL_SPECS[tool_id]
    if not spec.provider_key:
        return False
    return _tool_provider_enabled(tool_id, config)


def _list_config(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return ()


def _tool_ignored_by_runner_config(tool_id: str, config: Optional[Mapping[str, Any]]) -> bool:
    runner_cfg = _runner_config(config)
    ignored = {normalize_tool_id(item) or item for item in _list_config(runner_cfg.get("ignored_tools"))}
    ignored.update(normalize_tool_id(item) or item for item in _list_config(runner_cfg.get("ignore_tools")))
    tool_cfg = _runner_tool_config(config, tool_id)
    return tool_id in ignored or _bool_config(tool_cfg.get("enabled"), default=True) is False


def _tool_is_usable(
    tool_id: str,
    config: Optional[Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    prompt: str,
    task_kind: TaskKind,
    require_executable: bool,
) -> Tuple[bool, str, Tuple[str, ...], str, Optional[str]]:
    candidate = candidates.get(tool_id)
    candidate_command = candidate.get("command") if candidate else None
    
    routed_prompt = prepare_prompt_for_tool(tool_id, prompt, task_kind, config)
    command, stdin_text = build_command(tool_id, routed_prompt, config, candidate_command)
    if _tool_ignored_by_runner_config(tool_id, config):
        return False, "ignored by runner config", command, routed_prompt, stdin_text
    if not _tool_provider_enabled(tool_id, config):
        return False, "disabled by provider config", command, routed_prompt, stdin_text
    if require_executable and not _executable_exists(command):
        return False, "command not found", command, routed_prompt, stdin_text

    spec = TOOL_SPECS[tool_id]
    if candidate:
        headroom = candidate.get("headroom_pct")
        if isinstance(headroom, (int, float)):
            return True, f"quota available ({headroom:.0f}% left)", command, routed_prompt, stdin_text
        return True, "quota available", command, routed_prompt, stdin_text

    if spec.quota_optional and _is_enabled_without_quota(tool_id, config):
        return True, "enabled; no hard quota source", command, routed_prompt, stdin_text

    # If the user configured an explicit runner command, allow it even when no
    # first-party quota candidate exists. This keeps custom/local tools usable.
    if _runner_tool_config(config, tool_id):
        return True, "configured runner tool", command, routed_prompt, stdin_text

    return False, "no usable quota", command, routed_prompt, stdin_text


def route_prompt(
    prompt: str,
    config: Optional[Mapping[str, Any]] = None,
    preferred_tool: Optional[str] = None,
    quota_data: Optional[Mapping[str, Any]] = None,
    require_executable: bool = True,
) -> RouteDecision:
    """Choose a runner tool for ``prompt``."""

    if not prompt.strip():
        raise ValueError("prompt is required")

    task_kind = classify_prompt(prompt)
    quota = dict(quota_data) if quota_data is not None else collect_quota_data(config)
    candidates = _candidate_tools(quota)

    forced = normalize_tool_id(preferred_tool)
    if forced and forced not in TOOL_SPECS:
        raise ValueError(f"unsupported runner tool: {preferred_tool}")

    chain: Iterable[str]
    if forced:
        chain = (forced,)
    else:
        chain = ROUTE_PREFERENCES.get(task_kind, ROUTE_PREFERENCES["general"])

    rejected: List[str] = []
    for tool_id in chain:
        usable, reason, command, routed_prompt, stdin_text = _tool_is_usable(
            tool_id, config, candidates, prompt, task_kind, require_executable=require_executable
        )
        if usable:
            spec = TOOL_SPECS[tool_id]
            fallback_chain = tuple(t for t in chain if t != tool_id)
            return RouteDecision(
                tool_id=tool_id,
                label=spec.label,
                task_kind=task_kind,
                reason=reason,
                fallback_chain=fallback_chain,
                command=command,
                routed_prompt=routed_prompt,
                stdin_text=stdin_text,
            )
        rejected.append(f"{tool_id} ({reason})")

    if forced:
        raise RuntimeError(f"{forced} is not usable: {', '.join(rejected)}")
    raise RuntimeError("no usable runner tool found: " + "; ".join(rejected))


def shell_join(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _redact_command(command: Sequence[str]) -> Tuple[str, ...]:
    """Return a copy of command with environment assignment values masked."""
    redacted = []
    for part in command:
        if "=" in part and not part.startswith("/") and part not in ("env", "/usr/bin/env"):
            key, _, _ = part.partition("=")
            if key and key.isidentifier():
                redacted.append(f"{key}=[REDACTED]")
                continue
        redacted.append(part)
    return tuple(redacted)


def run_task(
    prompt: str,
    config: Optional[Mapping[str, Any]] = None,
    preferred_tool: Optional[str] = None,
    dry_run: bool = False,
    cwd: Optional[str] = None,
    no_color: bool = False,
    require_executable: bool = True,
) -> int:
    """Route and execute a prompt. Returns the child process exit code."""

    decision = route_prompt(
        prompt,
        config=config,
        preferred_tool=preferred_tool,
        require_executable=require_executable,
    )

    print_c(f"\n  LimitLens runner → {decision.label}", "\033[1;36m", no_color)
    print_c(f"  task:   {decision.task_kind}", "\033[90m", no_color)
    print_c(f"  why:    {decision.reason}", "\033[90m", no_color)
    print_c(f"  cmd:    {shell_join(_redact_command(decision.command))}", "\033[90m", no_color)
    if decision.stdin_text is not None:
        print_c("  input:  prompt via stdin", "\033[90m", no_color)
    if decision.routed_prompt != prompt:
        print_c("  prompt: adapted by runner config", "\033[90m", no_color)
    if decision.fallback_chain:
        print_c(f"  backup: {', '.join(decision.fallback_chain)}", "\033[90m", no_color)

    if dry_run:
        print_c("  dry-run: not launching agent\n", "\033[33m", no_color)
        return 0

    completed = subprocess.run(
        list(decision.command),
        input=decision.stdin_text,
        text=decision.stdin_text is not None,
        cwd=cwd,
        check=False,
    )
    return int(completed.returncode)
