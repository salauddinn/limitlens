"""
Usage tracker: compute usage from snapshots and handle import/export.

Rather than maintaining a mutable state file (which is prone to race conditions),
usage is computed dynamically from the append-only snapshots log collected by
waste_tracker.py. Imported historical data is merged on the fly for display.
"""

import json
import os

from . import waste_tracker

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
        os.makedirs(os.path.dirname(IMPORTED_USAGE_PATH), exist_ok=True)
        tmp_path = IMPORTED_USAGE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, IMPORTED_USAGE_PATH)
    except OSError:
        pass

def compute_daily_usage(days=365):
    """
    Compute daily usage by playing back snapshots from snapshots.jsonl.
    Returns: dict[date_str] -> dict[key] -> usage_float
    """
    rows = waste_tracker._load_snapshots()
    if not rows:
        return {}

    by_key = {}
    for row in rows:
        key = row["key"]
        by_key.setdefault(key, []).append(row)

    history = {}
    
    for key, series in by_key.items():
        series.sort(key=lambda r: r["_ts"])
        for prev, curr in zip(series, series[1:]):
            date_str = curr["_ts"].strftime("%Y-%m-%d")
            usage = 0.0

            if waste_tracker._is_reset_event(prev, curr):
                # When a reset happens, we assume usage was the remainder in the old bucket
                # plus what we see used in the new bucket. BUT waste_tracker assumes the 
                # remainder in the old bucket was "wasted" (unused). 
                # To align with not overcounting, we only count the usage we can verify:
                # which is the usage in the new bucket (100 - curr.pct_left).
                usage = 100.0 - (curr.get("pct_left") or 0.0)
            else:
                p_val = prev.get("pct_left") or 0.0
                c_val = curr.get("pct_left") or 0.0
                if c_val < p_val:
                    usage = p_val - c_val
            
            if usage > 0:
                if date_str not in history:
                    history[date_str] = {}
                history[date_str][key] = round(history[date_str].get(key, 0.0) + usage, 2)
                
    return history

def _get_merged_history():
    """Merge dynamically computed usage with imported historical usage."""
    live = compute_daily_usage()
    imported = _load_imported_data()
    
    merged = {}
    all_dates = set(live.keys()) | set(imported.keys())
    
    for date_str in all_dates:
        merged[date_str] = {}
        for k, v in imported.get(date_str, {}).items():
            merged[date_str][k] = v
        for k, v in live.get(date_str, {}).items():
            prev_v = merged[date_str].get(k, 0.0)
            merged[date_str][k] = max(prev_v, v)
            
    return merged

def _load_data():
    """
    Backward compatibility for cli.py which prints raw data.
    """
    return {"version": 2, "history": _get_merged_history()}

def record_usage(result):
    """
    Deprecated: Usage is now derived dynamically from waste_tracker snapshots.
    This function remains as a no-op for backward compatibility with cli.py.
    """
    pass

def export_usage(export_path):
    history = _get_merged_history()
    export_data = {"version": 2, "history": history}
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
            
        incoming_history = new_data.get("history", {})
        if not incoming_history and not new_data.get("version"):
            if any(isinstance(v, dict) for v in new_data.values()):
                incoming_history = new_data
                
        imported = _load_imported_data()
        
        for date_str, daily_usage in incoming_history.items():
            if date_str not in imported:
                imported[date_str] = {}
            for k, v in daily_usage.items():
                imported[date_str][k] = max(imported[date_str].get(k, 0.0), v)
                
        _save_imported_data(imported)
        return True
    except (json.JSONDecodeError, OSError):
        return False

def display_usage_report(args, print_c):
    history = _get_merged_history()
    print_c("\n  ═══ Usage Tracking Report ═══", "\033[1;35m", args.no_color)

    if not history:
        print_c("    No usage history recorded yet.", "\033[90m", args.no_color)
        return

    for date_str in sorted(history.keys(), reverse=True)[:7]: # Last 7 days
        print_c(f"\n  Date: {date_str}", "\033[1m", args.no_color)
        daily = history[date_str]

        if not daily:
            print_c("    No usage", "\033[90m", args.no_color)
            continue

        items = sorted(daily.items(), key=lambda x: -x[1])
        for key, usage in items:
            color = "\033[32m" if usage < 20 else "\033[33m" if usage < 80 else "\033[31m"
            if args.no_color:
                print(f"    {key:<40} {usage:>6.1f}% used")
            else:
                print(f"    {key:<40} {color}{usage:>6.1f}% used\033[0m")
