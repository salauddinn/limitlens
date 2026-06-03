"""
Usage tracker: maintain an accurate history of usage and quota consumption.
Stores data in a simple JSON format to allow easy import and export.
Computes usage by observing deltas between limitlens runs.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

USAGE_PATH = os.environ.get("LIMITLENS_USAGE_PATH") or os.path.expanduser("~/.cache/limitlens/usage.json")

def _load_data():
    if not os.path.exists(USAGE_PATH):
        return {"version": 1, "state": {}, "history": {}}
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "state" not in data:
                data["state"] = {}
            if "history" not in data:
                data["history"] = {}
            return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "state": {}, "history": {}}

def _save_data(data):
    try:
        os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
        # Write atomically
        tmp_path = USAGE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, USAGE_PATH)
    except OSError:
        pass

def export_usage(export_path):
    data = _load_data()
    try:
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except OSError:
        return False

def import_usage(import_path):
    try:
        with open(import_path, "r", encoding="utf-8") as f:
            new_data = json.load(f)

        data = _load_data()

        # Merge state
        for k, v in new_data.get("state", {}).items():
            if k not in data["state"] or v.get("ts", 0) > data["state"][k].get("ts", 0):
                data["state"][k] = v

        # Merge history
        for date_str, daily_usage in new_data.get("history", {}).items():
            if date_str not in data["history"]:
                data["history"][date_str] = {}
            for k, v in daily_usage.items():
                data["history"][date_str][k] = data["history"][date_str].get(k, 0) + v

        _save_data(data)
        return True
    except (json.JSONDecodeError, OSError):
        return False

def _reset_at_seconds(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            return None
    return None

def _extract_snapshots(result):
    """Extract current quota state from the result."""
    snapshots = {}
    ts = datetime.now(timezone.utc).timestamp()

    # Codex
    codex = result.get("codex") or {}
    for acc in codex.get("accounts", []):
        if "error" in acc:
            continue
        for lim in acc.get("limits", []):
            pct_left = lim.get("left_percent")
            if pct_left is not None:
                key = f"codex-{acc['name']}::{lim['label']}"
                snapshots[key] = {
                    "pct_left": float(pct_left),
                    "reset_at": _reset_at_seconds(lim.get("reset_time")),
                    "ts": ts
                }

    # Antigravity
    ag = result.get("antigravity") or {}
    for prof in ag.get("profiles", []):
        if prof.get("status") != "running":
            continue
        for m in prof.get("models", []):
            pct_left = m.get("pct_left")
            if pct_left is not None:
                key = f"antigravity:{prof['name']}::{m.get('label', '')}"
                snapshots[key] = {
                    "pct_left": float(pct_left),
                    "reset_at": _reset_at_seconds(m.get("reset_time")),
                    "ts": ts
                }

    # Amp / Pioneer / Custom could also be tracked if they provide pct_left
    for tool_name in ["amp", "pioneer", "custom"]:
        tool_data = result.get(tool_name) or {}
        for item in tool_data.get("limits", []) or tool_data.get("tiers", []):
            pct_left = item.get("pct_left")
            if pct_left is not None:
                key = f"{tool_name}::{item.get('name', '')}"
                snapshots[key] = {
                    "pct_left": float(pct_left),
                    "reset_at": _reset_at_seconds(item.get("reset_time") or item.get("resets_at")),
                    "ts": ts
                }

    return snapshots

def record_usage(result):
    """
    Compare current snapshots to previous state to compute and record usage.
    """
    data = _load_data()
    snapshots = _extract_snapshots(result)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today not in data["history"]:
        data["history"][today] = {}

    for key, current in snapshots.items():
        prev = data["state"].get(key)

        if prev:
            # Calculate usage
            usage = 0.0

            # Did it reset?
            # A reset happens if pct_left increased significantly OR reset_at moved forward
            pct_jump = current["pct_left"] - prev["pct_left"]

            p_reset = prev.get("reset_at")
            c_reset = current.get("reset_at")
            c_ts = current["ts"]

            is_reset = False
            if pct_jump >= 30: # 30% jump
                is_reset = True
            elif p_reset and c_reset and c_ts >= p_reset and c_reset > p_reset + 60:
                is_reset = True

            if is_reset:
                # We assume we used from 100% down to current pct_left after reset
                # Plus whatever we used before the reset (which we missed, unless we just count it as prev)
                # Actually, simplest accurate tracking:
                usage = 100.0 - current["pct_left"]
            else:
                if current["pct_left"] < prev["pct_left"]:
                    usage = prev["pct_left"] - current["pct_left"]

            if usage > 0:
                data["history"][today][key] = round(data["history"][today].get(key, 0.0) + usage, 2)

        data["state"][key] = current

    _save_data(data)

def display_usage_report(args, print_c):
    data = _load_data()
    print_c("\n  ═══ Usage Tracking Report ═══", "\033[1;35m", args.no_color)

    if not data["history"]:
        print_c("    No usage history recorded yet.", "\033[90m", args.no_color)
        return

    for date_str in sorted(data["history"].keys(), reverse=True)[:7]: # Last 7 days
        print_c(f"\n  Date: {date_str}", "\033[1m", args.no_color)
        daily = data["history"][date_str]

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
