"""
Provider registry — maps tool names to (get_data, display_text) pairs.

Each provider module exposes:
    get_<tool>_data(args, ...) -> dict
    display_<tool>_text(data, args) -> None
"""

from .codex import get_codex_data, display_codex_text, refresh_accounts, refresh_all_accounts
from .amp import get_amp_data, display_amp_text
from .antigravity import get_antigravity_data, display_antigravity_text
from .observed import get_opencode_data, display_opencode_text, get_pi_data, display_pi_text, get_kilo_data, display_kilo_text, get_claude_data, display_claude_text, get_copilot_cli_usage
from .pioneer import get_pioneer_data, display_pioneer_text
from .commandcode import get_commandcode_data, display_commandcode_text
from .custom import get_custom_data, display_custom_text
from .cursor import get_cursor_data, display_cursor_text
from .cline import get_cline_data, display_cline_text

PROVIDERS = {
    "codex":       (get_codex_data, display_codex_text),
    "amp":         (get_amp_data, display_amp_text),
    "antigravity": (get_antigravity_data, display_antigravity_text),
    "opencode":    (get_opencode_data, display_opencode_text),
    "pi":          (get_pi_data, display_pi_text),
    "kilo":        (get_kilo_data, display_kilo_text),
    "claude":  (get_claude_data, display_claude_text),
    "copilot_cli": (get_copilot_cli_usage, None),
    "pioneer":     (get_pioneer_data, display_pioneer_text),
    "commandcode": (get_commandcode_data, display_commandcode_text),
    "custom":      (get_custom_data, display_custom_text),
    "cursor":      (get_cursor_data, display_cursor_text),
    "cline":       (get_cline_data, display_cline_text),
}

__all__ = [
    "PROVIDERS",
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
]
