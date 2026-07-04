"""
Shared utilities, display helpers, and configuration for limitlens.

Everything in this module is a leaf dependency — it imports only from
the standard library and is imported by providers, cli, recommendations,
and waste_tracker.
"""

import os
import re
from datetime import datetime, timezone
import contextlib
import time
import threading

# ── Redaction helpers ───────────────────────────────────────────────────────

def redact_email(email):
    if not email or "@" not in email:
        return email
    user, domain = email.split("@", 1)
    if len(user) > 2:
        return f"{user[:2]}***@{domain}"
    return f"***@{domain}"

def redact_path(path):
    if not path:
        return path
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    path = re.sub(r'(\.codex-)[^/]+', r'\1***', path)
    return path

def redact_text(text):
    if not text:
        return text
    redacted = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", lambda m: redact_email(m.group(0)), text)
    home = os.path.expanduser("~")
    if home and home != "/":
        redacted = redacted.replace(home, "~")
    redacted = re.sub(r'(\.codex-)[^/\s]+', r'\1***', redacted)
    return redacted

# ── Formatting helpers ──────────────────────────────────────────────────────

def bar(pct, width=20, no_color=False):
    import math
    try:
        pct = float(pct)
        if math.isnan(pct):
            pct = 0.0
    except (ValueError, TypeError):
        pct = 0.0
    pct = max(0.0, min(100.0, pct))
    filled_fraction = (pct / 100.0) * width
    full_blocks = int(filled_fraction)
    fraction = filled_fraction - full_blocks
    
    blocks = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]
    fraction_idx = int(fraction * 8)
    
    bar_str = "█" * full_blocks
    if full_blocks < width:
        extra_char = blocks[fraction_idx]
        bar_str += extra_char
        left = width - full_blocks - (1 if extra_char else 0)
        if left > 0:
            bar_str += "░" * left
            
    color = ""
    reset = ""
    if not no_color:
        color = "\033[32m" if pct < 50 else "\033[33m" if pct < 85 else "\033[31m"
        reset = "\033[0m"
    return f"{color}{bar_str}{reset}"

def format_date_pretty(dt):
    day = dt.day
    if 11 <= (day % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    time_str = dt.strftime("%I:%M %p").lstrip("0")
    return f"{dt.strftime('%b')} {day}{suffix} {time_str}"

def format_timestamp(dt):
    return dt.astimezone().strftime("%Y-%m-%d %I:%M:%S %p %Z")

def parse_to_utc(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def is_reset_passed(reset_time):
    if not reset_time:
        return False
    try:
        return parse_to_utc(reset_time) <= datetime.now(timezone.utc)
    except (TypeError, ValueError, OSError):
        return False

def pluralize(count, unit):
    return f"{count} {unit}{'' if count == 1 else 's'}"

def humanize_remaining(delta):
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 3 * 3600:
        total_minutes = max(1, total_seconds // 60)
        hours, minutes = divmod(total_minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return pluralize(hours, "hour")
        return f"{minutes}m"
    for unit, seconds_per_unit in (("day", 86400), ("hour", 3600), ("minute", 60)):
        count = total_seconds // seconds_per_unit
        if count >= 1:
            return pluralize(count, unit)
    return pluralize(1, "minute")

def fmt_reset(value, is_stale=False):
    if not value:
        return "—"
    try:
        reset_local = parse_to_utc(value).astimezone()
    except (TypeError, ValueError, OSError):
        return str(value)

    now = datetime.now().astimezone()
    if reset_local <= now:
        if is_stale:
            return "likely reset (open to refresh)"
        return "resetting soon"
    return f"{humanize_remaining(reset_local - now)} left to reset"

def _fmt_tokens(n):
    """Format token counts as 1.2M / 340K / 850."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if abs_n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

# ── Icon/Emoji helpers ───────────────────────────────────────────────────────

TOOL_ICONS = {
    "antigravity": "🪐",
    "antigrav": "🪐",
    "codex": "⚡",
    "amp": "🔥",
    "pioneer": "🧭",
    "kilo": "🔶",
    "commandcode": "🖥️",
    "claude": "🤖",
    "copilot": "✈️",
    "cursor": "🖱️",
}

CUSTOM_KEYWORDS = [
    ("claude",      "🧠"),
    ("anthropic",   "🧠"),
    ("gpt",         "🌀"),
    ("openai",      "🌀"),
    ("gemini",      "💎"),
    ("google",      "💎"),
    ("mistral",     "🌪️"),
    ("llama",       "🦙"),
    ("ollama",      "🦙"),
    ("groq",        "⚙️"),
    ("perplexity",  "🔍"),
    ("cohere",      "🔵"),
    ("deepseek",    "🐋"),
    ("qwen",        "🌸"),
    ("local",       "🏠"),
    ("code",        "💻"),
    ("chat",        "💬"),
    ("agent",       "🤖"),
    ("api",         "🔌"),
]

_FALLBACK_POOL = ["🟣", "🟤", "🔺", "🔸", "🔹", "⭐", "🎯", "🧩", "🪩", "🎲"]

def get_tool_icon(tool_key="", name="", section=""):
    """Return a unique emoji: known tool → fixed icon, custom → keyword
    match on name, or deterministic pool pick based on name adler32 hash."""
    # 1. Known LimitLens tools — match on tool_key, name, or section
    # 1a. Exact match on tool_key first (for short/ambiguous keys like "claude")
    EXACT_TOOL_KEYS = {
        "claude", "pi",
    }
    if tool_key and tool_key.lower() in TOOL_ICONS and tool_key.lower() in EXACT_TOOL_KEYS:
        return TOOL_ICONS[tool_key.lower()]

    # 1b. Substring match on tool_key, name, section for other known tools
    for source in (tool_key, name, section):
        key = (source or "").lower()
        for k, icon in TOOL_ICONS.items():
            if k in EXACT_TOOL_KEYS:
                continue  # already handled above; skip substring match
            if k in key:
                return icon

    # 2. Custom tool — try keyword match on the name
    name_lower = (name or "").lower()
    for keyword, icon in CUSTOM_KEYWORDS:
        if keyword in name_lower:
            return icon

    # 3. Deterministic fallback: same name always gets same icon
    import zlib
    idx = zlib.adler32(name_lower.encode('utf-8')) % len(_FALLBACK_POOL)
    return _FALLBACK_POOL[idx]


# ── Terminal display helpers ────────────────────────────────────────────────

def print_c(text, color_code, no_color=False, end="\n"):
    if no_color:
        print(text, end=end)
    else:
        print(f"{color_code}{text}\033[0m", end=end)

def is_plain(args):
    return bool(getattr(args, "plain", False))

def plain_icon(icon, args):
    return "" if is_plain(args) else icon

def is_verbose(args):
    return bool(getattr(args, "verbose", False))

def section(title, args, color="\033[1;36m"):
    print_c(f"\n  {title}", color, args.no_color)

def identity_line(name, detail, args, status=None):
    status_colors = {
        "running": "\033[32m",
        "stale": "\033[33m",
        "stopped": "\033[33m",
    }
    if args.no_color:
        suffix = f"  {status}" if status else f"  {detail}" if detail else ""
        print(f"\n  {name}{suffix}")
        return
    styled_name = f"\033[1m{name}\033[0m"
    if status:
        color = status_colors.get(status, "\033[90m")
        print(f"\n  {styled_name}  {color}{status}\033[0m")
    elif detail:
        print(f"\n  {styled_name}  \033[90m{detail}\033[0m")
    else:
        print(f"\n  {styled_name}")

def print_warning(message, args):
    msg_str = str(message or "")
    print_c(f"    ⚠ {msg_str.replace('⚠ ', '')}", "\033[33m", args.no_color)

def print_error(message, args):
    print_c(f"    ✖ {message.replace('✖ ', '')}", "\033[31m", args.no_color)

def should_show_warning(message, args):
    return is_verbose(args) or "insecure local TLS fallback" not in (message or "")

def should_show_detail(args):
    return is_verbose(args)

_thread_local_locks = threading.local()

def _get_active_locks():
    if not hasattr(_thread_local_locks, "active"):
        _thread_local_locks.active = set()
    return _thread_local_locks.active

@contextlib.contextmanager
def file_lock(lock_path, timeout=5.0, delay=0.05):
    """A reentrant file-based directory lock using os.mkdir with timeout and cleanup."""
    active = _get_active_locks()
    if lock_path in active:
        yield
        return

    start_time = time.time()
    acquired = False
    while True:
        try:
            os.mkdir(lock_path)
            acquired = True
            active.add(lock_path)
            break
        except FileNotFoundError:
            parent_dir = os.path.dirname(lock_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
        except FileExistsError:
            if time.time() - start_time > timeout:
                try:
                    mtime = os.path.getmtime(lock_path)
                    if time.time() - mtime > 10.0:
                        try:
                            os.rmdir(lock_path)
                        except OSError:
                            pass
                        try:
                            os.mkdir(lock_path)
                            acquired = True
                            active.add(lock_path)
                            break
                        except FileExistsError:
                            continue
                except OSError:
                    pass
                raise TimeoutError(f"Could not acquire lock on {lock_path} within {timeout} seconds")
            time.sleep(delay)

    stop_event = threading.Event()
    def touch_lock():
        while not stop_event.wait(3.0):
            try:
                os.utime(lock_path, None)
            except OSError:
                break

    touch_thread = None
    if acquired:
        touch_thread = threading.Thread(target=touch_lock, daemon=True)
        touch_thread.start()

    try:
        yield
    finally:
        if acquired:
            stop_event.set()
            if touch_thread:
                touch_thread.join(timeout=1.0)
            active.discard(lock_path)
            try:
                os.rmdir(lock_path)
            except OSError:
                pass

from .config import (  # noqa: F401, E402
    DEFAULT_CONFIG,
    deep_merge,
    limitlens_config_path,
    validate_config_types,
    apply_env_overrides,
    load_limitlens_config,
    load_display_config,
    configured_days,
)
