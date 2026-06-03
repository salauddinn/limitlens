"""
Shared utilities, display helpers, and configuration for limitlens.

Everything in this module is a leaf dependency — it imports only from
the standard library and is imported by providers, cli, recommendations,
and waste_tracker.
"""

import json
import os
import re
from datetime import datetime, timezone

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
    pct = max(0.0, min(100.0, float(pct)))
    used  = int((pct / 100) * width)
    left  = width - used
    color = ""
    reset = ""
    if not no_color:
        color = "\033[32m" if pct < 60 else "\033[33m" if pct < 85 else "\033[31m"
        reset = "\033[0m"
    return f"{color}{'█' * used}{'░' * left}{reset}"

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

# ── Terminal display helpers ────────────────────────────────────────────────

def print_c(text, color_code, no_color=False, end="\n"):
    if no_color:
        print(text, end=end)
    else:
        print(f"{color_code}{text}\033[0m", end=end)

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

from .config import (
    DEFAULT_CONFIG,
    deep_merge,
    limitlens_config_path,
    validate_config_types,
    apply_env_overrides,
    load_limitlens_config,
    load_display_config,
    configured_days,
)

