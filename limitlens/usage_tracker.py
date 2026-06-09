"""
Usage tracker: compute usage from snapshots and handle import/export.

Rather than maintaining a mutable state file (which is prone to race conditions),
usage is computed dynamically from the append-only snapshots log collected by
waste_tracker.py. Imported historical data is merged on the fly for display.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from . import waste_tracker
from .providers.observed import display_opencode_text

IMPORTED_USAGE_PATH = os.environ.get("LIMITLENS_IMPORTED_USAGE_PATH") or os.path.expanduser("~/.cache/limitlens/imported_usage.json")
ANALYTICS_VERSION = 1

def _load_imported_data():
    if not os.path.exists(IMPORTED_USAGE_PATH):
        return {}
    try:
        with open(IMPORTED_USAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

def _save_imported_data(data):
    try:
        os.makedirs(os.path.dirname(IMPORTED_USAGE_PATH), mode=0o700, exist_ok=True)
        tmp_path = IMPORTED_USAGE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, IMPORTED_USAGE_PATH)
    except OSError:
        pass

def _snapshot_float(row, field):
    try:
        value = row.get(field)
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def _is_amp_key(key):
    return str(key).startswith("amp::")

def _is_amp_snapshot(row, key):
    return row.get("tool") == "amp" or _is_amp_key(key)


def _tool_from_key(key):
    key = str(key)
    if key.startswith("amp::"):
        return "amp"
    if key.startswith("codex-"):
        return "codex"
    if key.startswith("antigravity:"):
        return "antigravity"
    return key.split(":", 1)[0].split("-", 1)[0] or "unknown"


def _snapshot_unit_for_key(key):
    return "usd" if _is_amp_key(key) else "percent"

def compute_consolidated_usage(days, config=None):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = waste_tracker._load_snapshots_with_anchor(since)

    by_key = {}
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        if waste_tracker._snapshot_key_is_config_ignored(key, config):
            continue
        by_key.setdefault(key, []).append(row)

    usage_by_key = {}

    for key, series in by_key.items():
        series.sort(key=lambda r: r["_ts"])
        total_usage = 0.0

        for prev, curr in zip(series, series[1:]):
            if curr["_ts"] < since:
                continue

            usage = 0.0
            if _is_amp_snapshot(prev, key) or _is_amp_snapshot(curr, key):
                p_remaining = _snapshot_float(prev, "remaining")
                c_remaining = _snapshot_float(curr, "remaining")
                if p_remaining is not None and c_remaining is not None and c_remaining < p_remaining:
                    usage = p_remaining - c_remaining
            elif waste_tracker._is_reset_event(prev, curr):
                usage = 100.0 - float(curr.get("pct_left") or 0.0)
            else:
                p_val = float(prev.get("pct_left") or 0.0)
                c_val = float(curr.get("pct_left") or 0.0)
                if c_val < p_val:
                    usage = p_val - c_val

            total_usage += usage

        if total_usage > 0:
            usage_by_key[key] = round(total_usage, 2)

    # Also add in legacy imported_usage.json data for dates >= since
    imported = _load_imported_data()
    for date_str, daily_usage in imported.items():
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt >= since:
                for k, v in daily_usage.items():
                    if waste_tracker._snapshot_key_is_config_ignored(k, config):
                        continue
                    usage_by_key[k] = round(usage_by_key.get(k, 0.0) + v, 2)
        except ValueError:
            continue

    return usage_by_key

def _observed_totals(observed):
    totals = {
        "requests": 0,
        "cost": 0.0,
        "tokens": {
            "total": 0,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_read": 0,
            "cache_write": 0,
        },
        "credit_used": 0.0,
    }
    if not isinstance(observed, dict):
        return totals

    for source in observed.values():
        if not isinstance(source, dict):
            continue
        for limit in source.get("credit_limits") or []:
            try:
                totals["credit_used"] += float(limit.get("used") or 0.0)
            except (TypeError, ValueError):
                pass
        for window in source.get("windows") or []:
            for model in window.get("models") or []:
                try:
                    totals["requests"] += int(model.get("requests") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    totals["cost"] += float(model.get("cost") or 0.0)
                except (TypeError, ValueError):
                    pass
                tokens = model.get("tokens") or {}
                if not isinstance(tokens, dict):
                    continue
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                token_values = {
                    "total": tokens.get("total"),
                    "input": tokens.get("input"),
                    "output": tokens.get("output"),
                    "reasoning": tokens.get("reasoning"),
                    "cache_read": cache.get("read") if cache else tokens.get("cache_read"),
                    "cache_write": cache.get("write") if cache else tokens.get("cache_write"),
                }
                for key, value in token_values.items():
                    try:
                        totals["tokens"][key] += int(value or 0)
                    except (TypeError, ValueError):
                        pass
    totals["cost"] = round(totals["cost"], 6)
    totals["credit_used"] = round(totals["credit_used"], 6)
    return totals


def compute_usage_analytics(days=365, observed=None, config=None):
    """Return normalized usage analytics for CLI text and JSON reports."""
    snapshot_values = compute_consolidated_usage(days, config=config)
    raw_waste = waste_tracker.compute_waste(days, config=config)
    waste = {
        key: {"key": key, "tool": _tool_from_key(key), **data}
        for key, data in sorted(raw_waste.items())
    }

    snapshot_usage = {}
    snapshot_totals = {"percent": 0.0, "usd": 0.0, "keys": len(snapshot_values)}
    for key in sorted(snapshot_values):
        used = snapshot_values[key]
        unit = _snapshot_unit_for_key(key)
        snapshot_usage[key] = {
            "key": key,
            "tool": _tool_from_key(key),
            "used": used,
            "unit": unit,
        }
        snapshot_totals[unit] = round(snapshot_totals.get(unit, 0.0) + used, 2)

    waste_totals = {
        "keys": len(waste),
        "reset_count": sum(int(v.get("reset_count") or 0) for v in waste.values()),
    }
    analytics = {
        "metadata": {
            "version": ANALYTICS_VERSION,
            "days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "snapshot_usage": snapshot_usage,
        "waste": waste,
        "observed": observed or {},
        "totals": {
            "snapshot_usage": snapshot_totals,
            "waste": waste_totals,
            "observed": _observed_totals(observed or {}),
        },
    }
    return analytics


def _load_data(days=365):
    """
    Backward compatibility for cli.py and callers that expect loadable usage data.
    """
    analytics = compute_usage_analytics(days)
    analytics["version"] = 4
    analytics["consolidated_usage"] = {
        key: item["used"] for key, item in analytics["snapshot_usage"].items()
    }
    return analytics

def record_usage(result):
    """
    Deprecated: Usage is now derived dynamically from waste_tracker snapshots.
    This function remains as a no-op for backward compatibility with cli.py.
    """
    pass

def export_usage(export_path):
    rows = waste_tracker._load_snapshots()
    export_rows = []
    for r in rows:
        row_copy = r.copy()
        row_copy.pop("_ts", None)
        export_rows.append(row_copy)

    export_data = {"version": 3, "snapshots": export_rows}
    try:
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2)
        return True
    except OSError:
        return False

def import_usage(import_path):
    try:
        with open(import_path, "r", encoding="utf-8") as f:
            new_data = json.load(f)

        if "snapshots" in new_data:
            return waste_tracker.merge_snapshots(new_data["snapshots"])

        incoming_history = new_data.get("history", {})
        if not incoming_history and not new_data.get("version"):
            if any(isinstance(v, dict) for v in new_data.values()):
                incoming_history = new_data

        imported = _load_imported_data()

        for date_str, daily_usage in incoming_history.items():
            if date_str not in imported:
                imported[date_str] = {}
            for k, v in daily_usage.items():
                imported[date_str][k] = round(imported[date_str].get(k, 0.0) + v, 2)

        _save_imported_data(imported)
        return True
    except (json.JSONDecodeError, OSError):
        return False

def has_observed(data):
    if not data: return False
    for k in ["opencode", "pi", "copilot_cli"]:
        src = data.get(k, {})
        if src.get("credit_limits") or any(w.get("models") for w in src.get("windows", [])): return True
        if "error" in src and k == "opencode": return True
    return False


def _color(text, color, no_color):
    return text if no_color else f"{color}{text}\033[0m"


def _friendly_snapshot_label(key):
    key = str(key)
    if key.startswith("amp::"):
        return key.replace("amp::", "Amp ", 1)
    if key.startswith("antigravity:"):
        rest = key.replace("antigravity:", "", 1)
        return f"Antigravity {rest.replace('::', ' / ')}"
    if key.startswith("codex-"):
        rest = key.replace("codex-", "", 1)
        return f"Codex {rest.replace('::', ' / ')}"
    return key.replace("::", " / ")


def _shorten(text, width):
    text = str(text)
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _usage_health(used):
    if used >= 80:
        return "high"
    if used >= 40:
        return "active"
    if used > 0:
        return "light"
    return "idle"


def _bar(value, width=10):
    value = max(0.0, min(100.0, float(value or 0.0)))
    filled = int(round((value / 100.0) * width))
    return "█" * filled + "░" * (width - filled)


def _box(title, lines, no_color=False):
    width = max([len(title) + 4] + [len(line) for line in lines])
    top = f"╭─ {title} " + "─" * max(0, width - len(title) - 3) + "╮"
    bottom = "╰" + "─" * (len(top) - 2) + "╯"
    body = [f"│ {line:<{len(top) - 4}} │" for line in lines]
    return "\n".join([top] + body + [bottom])


def _waste_summary(avg, resets):
    if resets <= 0:
        return "no resets yet"
    verdict = waste_tracker._verdict(avg)
    return f"{avg:.0f}% unused avg · {verdict}"


def _fmt_snapshot_total(totals):
    parts = []
    if totals.get("percent"):
        parts.append(f"{totals['percent']:.1f}% quota used")
    if totals.get("usd"):
        parts.append(f"${totals['usd']:.2f} Amp used")
    if not parts:
        parts.append("no snapshot usage")
    return " · ".join(parts)


def _fmt_tokens(value):
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _fmt_observed_total(totals):
    requests = int(totals.get("requests") or 0)
    cost = float(totals.get("cost") or 0.0)
    token_total = int((totals.get("tokens") or {}).get("total") or 0)
    parts = []
    if requests:
        parts.append(f"{requests} requests")
    if token_total:
        parts.append(f"{_fmt_tokens(token_total)} tokens")
    if cost:
        parts.append(f"${cost:.2f}")
    return " · ".join(parts) if parts else "no observed spend"


def _observed_activity_rows(observed):
    rows = []
    if not isinstance(observed, dict):
        return rows
    for source_name, source in observed.items():
        if not isinstance(source, dict):
            continue
        for window in source.get("windows") or []:
            for model in window.get("models") or []:
                tokens = model.get("tokens") or {}
                total_tokens = int(tokens.get("total") or 0) if isinstance(tokens, dict) else 0
                requests = int(model.get("requests") or 0)
                cost = float(model.get("cost") or 0.0)
                if not (total_tokens or requests or cost):
                    continue
                provider = model.get("provider") or source_name
                name = model.get("model") or "unknown"
                rows.append({
                    "label": f"{source_name} {provider}/{name}",
                    "requests": requests,
                    "tokens": total_tokens,
                    "cost": cost,
                })
    rows.sort(key=lambda r: (r["cost"], r["tokens"], r["requests"]), reverse=True)
    return rows


def display_consolidated_report(args, print_c, opencode_data=None, analytics=None, config=None):
    analytics = analytics or compute_usage_analytics(args.days, observed=opencode_data, config=config)
    usage_by_key = {
        key: item.get("used", 0.0)
        for key, item in analytics.get("snapshot_usage", {}).items()
    }
    waste_by_key = analytics.get("waste", {})
    observed = analytics.get("observed") or opencode_data or {}

    totals = analytics.get("totals", {})

    if not usage_by_key and not waste_by_key and not has_observed(observed):
        print_c(f"\n  ═══ Usage Summary · last {args.days} days ═══", "\033[1;35m", args.no_color)
        print_c("    No usage history recorded yet.", "\033[90m", args.no_color)
        print_c("    Run `limitlens --record` occasionally to build a history.", "\033[90m", args.no_color)
        return

    snapshot_totals = totals.get("snapshot_usage", {})
    observed_total = _fmt_observed_total(totals.get("observed", {}))
    waste_total = totals.get("waste", {})
    reset_count = int(waste_total.get("reset_count") or 0)
    quota_used = float(snapshot_totals.get("percent") or 0.0)
    amp_used = float(snapshot_totals.get("usd") or 0.0)
    quota_count = int(snapshot_totals.get("keys") or 0)
    overall = "Good activity" if quota_used or amp_used or has_observed(observed) else "No recent activity"

    print("\n  " + _box(
        f"LimitLens Usage · last {args.days} days",
        [
            f"Overall        {overall}",
            f"Quota usage    {_bar(min(quota_used, 100))}  {quota_used:.1f}% across {quota_count} quotas",
            f"Amp spend      ${amp_used:.2f}",
            f"API spend      {observed_total}",
            f"Waste risk     {reset_count} reset{'s' if reset_count != 1 else ''} had leftover quota",
        ],
        args.no_color,
    ).replace("\n", "\n  "))

    all_keys = set(usage_by_key.keys()) | set(waste_by_key.keys())
    quota_items = [k for k in all_keys if not _is_amp_key(k)]
    amp_items = [k for k in all_keys if _is_amp_key(k)]

    if quota_items:
        print_c("\n  Quota usage", "\033[1;36m", args.no_color)
        items = sorted(quota_items, key=lambda k: usage_by_key.get(k, 0.0), reverse=True)
        max_rows = len(items) if getattr(args, "verbose", False) else 5
        for key in items[:max_rows]:
            usage = float(usage_by_key.get(key, 0.0))
            label = _shorten(_friendly_snapshot_label(key), 30)
            color = "\033[32m" if usage < 40 else "\033[33m" if usage < 80 else "\033[31m"
            used_text = _color(f"{_bar(usage)}  {usage:5.1f}%", color, args.no_color)
            print(f"    {label:<30} {used_text}   {_usage_health(usage)}")
        hidden = len(items) - max_rows
        if hidden > 0:
            print_c(f"    +{hidden} more (use --verbose)", "\033[90m", args.no_color)

    activity_rows = _observed_activity_rows(observed)
    if activity_rows:
        print_c("\n  Tokens / requests", "\033[1;36m", args.no_color)
        max_rows = len(activity_rows) if getattr(args, "verbose", False) else 5
        for row in activity_rows[:max_rows]:
            cost_text = f" · ${row['cost']:.2f}" if row["cost"] else ""
            print(
                f"    {_shorten(row['label'], 30):<30} "
                f"{_fmt_tokens(row['tokens']):>7} tokens · {row['requests']:>4} req{cost_text}"
            )
        hidden = len(activity_rows) - max_rows
        if hidden > 0:
            print_c(f"    +{hidden} more (use --verbose)", "\033[90m", args.no_color)
        if quota_items:
            print_c("    Note: token/request data comes from observed providers, not quota snapshots.", "\033[90m", args.no_color)

    if amp_items or has_observed(observed):
        print_c("\n  Spend", "\033[1;36m", args.no_color)
        for key in sorted(amp_items):
            usage = float(usage_by_key.get(key, 0.0))
            print(f"    {_shorten(_friendly_snapshot_label(key), 30):<30} ${usage:.2f} used")
        if has_observed(observed):
            print(f"    {'OpenCode / Pi / Copilot':<30} {observed_total}")

    warning_items = sorted(
        [(k, v) for k, v in waste_by_key.items() if float(v.get("avg_wasted_pct") or 0.0) >= 30],
        key=lambda kv: float(kv[1].get("avg_wasted_pct") or 0.0),
        reverse=True,
    )
    if warning_items:
        key, data = warning_items[0]
        avg = float(data.get("avg_wasted_pct") or 0.0)
        print_c("\n  Waste warning", "\033[1;36m", args.no_color)
        print(f"    {_friendly_snapshot_label(key)} reset with {avg:.0f}% unused on average.")
        print("    Action: use this quota earlier before reset.")
    else:
        print_c("\n  Tip: use `limitlens --waste` to see reset waste details.", "\033[90m", args.no_color)

    if getattr(args, "verbose", False) and all_keys:
        print_c("\n  Raw snapshot keys", "\033[90m", args.no_color)
        for key in sorted(all_keys):
            print_c(f"    {key}", "\033[90m", args.no_color)


def display_usage_report(args, print_c):
    """Backward-compatible alias for the consolidated report."""
    display_consolidated_report(args, print_c)
