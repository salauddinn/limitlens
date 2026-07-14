"""
Provider registry — maps tool names to (get_data, display_text) pairs.

Each provider module exposes:
    get_<tool>_data(args, ...) -> dict
    display_<tool>_text(data, args) -> None

``PROVIDER_DESCRIPTORS`` is the **single source of truth** for per-provider
metadata (label, CLI key, config key, default_enabled, display section).
All other sites that previously hand-coded these values (doctor rows,
enabled_count, tool_label dict, …) should read from this dict to prevent
drift (#5 / #6 from p1_breakdown.html).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from .codex import get_codex_data, display_codex_text, refresh_accounts, refresh_all_accounts
from .amp import get_amp_data, display_amp_text
from .antigravity import get_antigravity_data, display_antigravity_text
from .observed import (
    get_opencode_data, display_opencode_text,
    get_pi_data, display_pi_text,
    get_kilo_data, display_kilo_text,
    get_claude_data, display_claude_text,
    get_copilot_cli_usage,
)
from .pioneer import get_pioneer_data, display_pioneer_text
from .commandcode import get_commandcode_data, display_commandcode_text
from .custom import get_custom_data, display_custom_text
from .cursor import get_cursor_data, display_cursor_text
from .cline import get_cline_data, display_cline_text
from .grok import get_grok_data, display_grok_text


@dataclass(frozen=True)
class ProviderDescriptor:
    """Immutable metadata for a single provider.

    Attributes
    ----------
    key:
        Canonical CLI key (matches ``--tool`` choices and result dict keys).
    label:
        Human-readable display label, e.g. ``"Codex"``.
    config_key:
        Key used in the config dict.  Usually the same as ``key`` but
        ``"custom"`` maps to ``"custom_tools"``.
    default_enabled:
        Whether this provider is enabled when no user config is present.
        **Must match** ``DEFAULT_CONFIG[config_key]["enabled"]`` in
        ``limitlens/config.py`` — this is the single source of truth that
        eliminates the drift documented in #6.
    fetch:
        ``get_<key>_data(args, config)`` callable.
    display:
        ``display_<key>_text(data, args)`` callable, or ``None`` if the
        provider has no dedicated display function.
    display_section:
        ``"Quota"`` for providers with hard quota limits, ``"Observed"``
        for providers that track observed spend.
    aliases:
        Extra ``--tool`` aliases for this provider (e.g. ``("agy",)``).
    unit_hint:
        Primary unit shown in menubar rows: ``"%"``, ``"$"``, etc.
    """

    key: str
    label: str
    config_key: str
    default_enabled: bool
    fetch: Callable
    display: Optional[Callable]
    display_section: str  # "Quota" | "Observed"
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    unit_hint: str = "%"


# ---------------------------------------------------------------------------
# Single source of truth for all 13 providers.
#
# ``default_enabled`` values here MUST match ``DEFAULT_CONFIG[config_key]["enabled"]``
# in limitlens/config.py so that doctor rows, enabled_count, and menubar
# row-collection never disagree.
# ---------------------------------------------------------------------------
PROVIDER_DESCRIPTORS: dict = {
    "codex": ProviderDescriptor(
        key="codex", label="Codex", config_key="codex",
        default_enabled=True,
        fetch=get_codex_data, display=display_codex_text,
        display_section="Quota",
    ),
    "amp": ProviderDescriptor(
        key="amp", label="Amp", config_key="amp",
        default_enabled=True,
        # get_amp_data takes only (args) — normalize to (args, config)
        fetch=lambda args, config: get_amp_data(args),
        display=display_amp_text,
        display_section="Quota", unit_hint="$",
    ),
    "antigravity": ProviderDescriptor(
        key="antigravity", label="Antigravity", config_key="antigravity",
        default_enabled=True,
        fetch=get_antigravity_data, display=display_antigravity_text,
        display_section="Quota",
        aliases=("agy",),
    ),
    "opencode": ProviderDescriptor(
        key="opencode", label="OpenCode", config_key="opencode",
        default_enabled=True,
        fetch=get_opencode_data, display=display_opencode_text,
        display_section="Observed", unit_hint="$",
    ),
    "pi": ProviderDescriptor(
        key="pi", label="Pi", config_key="pi",
        # config.py DEFAULT_CONFIG["pi"]["enabled"] = True
        default_enabled=True,
        fetch=get_pi_data, display=display_pi_text,
        display_section="Observed", unit_hint="$",
    ),
    "kilo": ProviderDescriptor(
        key="kilo", label="Kilo", config_key="kilo",
        # config.py DEFAULT_CONFIG["kilo"]["enabled"] = True
        default_enabled=True,
        fetch=get_kilo_data, display=display_kilo_text,
        display_section="Observed", unit_hint="$",
    ),
    "claude": ProviderDescriptor(
        key="claude", label="Claude", config_key="claude",
        # config.py DEFAULT_CONFIG["claude"]["enabled"] = True
        default_enabled=True,
        fetch=get_claude_data, display=display_claude_text,
        display_section="Observed", unit_hint="$",
    ),
    "copilot_cli": ProviderDescriptor(
        key="copilot_cli", label="Copilot CLI", config_key="copilot_cli",
        # config.py DEFAULT_CONFIG["copilot_cli"]["enabled"] = True
        default_enabled=True,
        # get_copilot_cli_usage takes only (config) — normalize to (args, config)
        fetch=lambda args, config: get_copilot_cli_usage(config),
        display=None,
        display_section="Observed", unit_hint="$",
    ),
    "cursor": ProviderDescriptor(
        key="cursor", label="Cursor", config_key="cursor",
        default_enabled=True,
        fetch=get_cursor_data, display=display_cursor_text,
        display_section="Quota",
    ),
    "cline": ProviderDescriptor(
        key="cline", label="Cline", config_key="cline",
        default_enabled=True,
        fetch=get_cline_data, display=display_cline_text,
        display_section="Quota",
    ),
    "pioneer": ProviderDescriptor(
        key="pioneer", label="Pioneer", config_key="pioneer",
        default_enabled=False,
        fetch=get_pioneer_data, display=display_pioneer_text,
        display_section="Quota",
    ),
    "commandcode": ProviderDescriptor(
        key="commandcode", label="CommandCode", config_key="commandcode",
        default_enabled=False,
        fetch=get_commandcode_data, display=display_commandcode_text,
        display_section="Quota", unit_hint="$",
    ),
    "custom": ProviderDescriptor(
        key="custom", label="Custom", config_key="custom_tools",
        default_enabled=False,
        fetch=get_custom_data, display=display_custom_text,
        display_section="Quota",
    ),
    "grok": ProviderDescriptor(
        key="grok", label="Grok", config_key="grok",
        default_enabled=False,
        fetch=get_grok_data, display=display_grok_text,
        display_section="Quota",
    ),
}


# Legacy flat dict kept for backward compatibility.
PROVIDERS = {
    "codex":       (get_codex_data, display_codex_text),
    "amp":         (get_amp_data, display_amp_text),
    "antigravity": (get_antigravity_data, display_antigravity_text),
    "opencode":    (get_opencode_data, display_opencode_text),
    "pi":          (get_pi_data, display_pi_text),
    "kilo":        (get_kilo_data, display_kilo_text),
    "claude":      (get_claude_data, display_claude_text),
    "copilot_cli": (get_copilot_cli_usage, None),
    "pioneer":     (get_pioneer_data, display_pioneer_text),
    "commandcode": (get_commandcode_data, display_commandcode_text),
    "custom":      (get_custom_data, display_custom_text),
    "cursor":      (get_cursor_data, display_cursor_text),
    "cline":       (get_cline_data, display_cline_text),
    "grok":        (get_grok_data, display_grok_text),
}

__all__ = [
    "PROVIDERS",
    "PROVIDER_DESCRIPTORS",
    "ProviderDescriptor",
    "get_codex_data", "display_codex_text", "refresh_accounts", "refresh_all_accounts",
    "get_amp_data", "display_amp_text",
    "get_antigravity_data", "display_antigravity_text",
    "get_opencode_data", "display_opencode_text", "get_pi_data", "display_pi_text",
    "get_kilo_data", "display_kilo_text",
    "get_claude_data", "display_claude_text", "get_copilot_cli_usage",
    "get_pioneer_data", "display_pioneer_text",
    "get_commandcode_data", "display_commandcode_text",
    "get_custom_data", "display_custom_text",
    "get_cursor_data", "display_cursor_text",
    "get_cline_data", "display_cline_text",
    "get_grok_data", "display_grok_text",
]
