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

def compute_consolidated_usage(days):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = waste_tracker._load_snapshots_with_anchor(since)

    by_key = {}
    for row in rows:
        key = row.get("key")
        if not key:
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
                    usage_by_key[k] = round(usage_by_key.get(k, 0.0) + v, 2)
        except ValueError:
            continue

    return usage_by_key

def _load_data(days=365):
    """
    Backward compatibility for cli.py which prints raw data.
    """
    return {"version": 4, "consolidated_usage": compute_consolidated_usage(days)}

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

def display_consolidated_report(args, print_c, opencode_data=None):
    usage_by_key = compute_consolidated_usage(args.days)
    waste_by_key = waste_tracker.compute_waste(args.days)

    print_c(f"\n  ═══ Unified Usage & Waste Report (last {args.days} days) ═══", "\033[1;35m", args.no_color)

    if not usage_by_key and not waste_by_key and not has_observed(opencode_data):
        print_c("    No usage history recorded yet.", "\033[90m", args.no_color)
        return

    all_keys = set(usage_by_key.keys()) | set(waste_by_key.keys())

    if all_keys:
        print_c("\n  Snapshot Usage (Codex / Antigravity / Amp)", "\033[1;36m", args.no_color)
        items = sorted(list(all_keys))
        for key in items:
            usage = usage_by_key.get(key, 0.0)
            waste_data = waste_by_key.get(key)

            if _is_amp_key(key):
                color = "\033[32m" if usage < 2 else "\033[33m" if usage < 10 else "\033[31m"
                usage_str = f"${usage:>6.2f} used"
                if not args.no_color:
                    usage_str = f"{color}{usage_str}\033[0m"
                print(f"    {key:<40} {usage_str}")
            else:
                color = "\033[32m" if usage < 20 else "\033[33m" if usage < 80 else "\033[31m"
                usage_str = f"{usage:>6.1f}% used"
                if not args.no_color:
                    usage_str = f"{color}{usage_str}\033[0m"

                waste_str = ""
                if waste_data:
                    avg = waste_data["avg_wasted_pct"]
                    resets = waste_data["reset_count"]
                    w_color = "\033[31m" if avg >= 60 else "\033[33m" if avg >= 30 else "\033[32m"
                    w_text = f"| {avg:>5.1f}% wasted (avg over {resets} resets)"
                    if not args.no_color:
                        w_text = f"| {w_color}{avg:>5.1f}% wasted\033[0m (avg over {resets} resets)"
                    waste_str = f"  {w_text}"

                print(f"    {key:<40} {usage_str}  {waste_str}")

        print_c(
            "    💡 Note: Mathematical tracking guarantees Used + Wasted = 100% of Quota per reset cycle.",
            "\033[90m", args.no_color,
        )

    if opencode_data:
        display_opencode_text(opencode_data, args)
