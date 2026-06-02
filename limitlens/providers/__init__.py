"""
Provider registry — maps tool names to (get_data, display_text) pairs.

Each provider module exposes:
    get_<tool>_data(args, ...) -> dict
    display_<tool>_text(data, args) -> None
"""

from .codex import get_codex_data, display_codex_text, refresh_accounts, refresh_all_accounts
from .amp import get_amp_data, display_amp_text
from .antigravity import get_antigravity_data, display_antigravity_text
from .observed import get_opencode_data, display_opencode_text
from .pioneer import get_pioneer_data, display_pioneer_text

PROVIDERS = {
    "codex":       (get_codex_data, display_codex_text),
    "amp":         (get_amp_data, display_amp_text),
    "antigravity": (get_antigravity_data, display_antigravity_text),
    "opencode":    (get_opencode_data, display_opencode_text),
    "pioneer":     (get_pioneer_data, display_pioneer_text),
}

__all__ = [
    "PROVIDERS",
    "get_codex_data", "display_codex_text", "refresh_accounts", "refresh_all_accounts",
    "get_amp_data", "display_amp_text",
    "get_antigravity_data", "display_antigravity_text",
    "get_opencode_data", "display_opencode_text",
    "get_pioneer_data", "display_pioneer_text",
]
