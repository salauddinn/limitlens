"""Codex provider — discovers ~/.codex-* accounts, parses session rate limits."""

import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from limitlens.core import (
    redact_path,
    parse_to_utc,
    is_reset_passed,
    fmt_reset,
    format_timestamp,
    bar,
    print_c,
    print_warning,
    print_error,
    is_verbose,
    identity_line,
    _fmt_tokens,
    load_display_config,
)


# ── Codex helpers ───────────────────────────────────────────────────────────

def discover_accounts():
    home = Path.home()
    accounts = {}
    default_codex = home / ".codex"
    if default_codex.is_dir():
        accounts["default"] = str(default_codex)
    for entry in sorted(home.glob(".codex-*")):
        if entry.is_dir():
            name = entry.name.removeprefix(".codex-")
            accounts[name] = str(entry)
    return accounts

def account_is_ignored(name, home, ignored_accounts):
    if isinstance(ignored_accounts, str):
        ignored_accounts = [ignored_accounts]
    elif not isinstance(ignored_accounts, list):
        return False

    label = "codex" if name == "default" else f"codex-{name}"
    candidates = {
        str(name).lower(),
        label.lower(),
        os.path.basename(home).lower(),
        os.path.expanduser(home).lower(),
    }
    for ignored in ignored_accounts:
        value = os.path.expanduser(str(ignored).strip()).lower()
        if value in candidates:
            return True
    return False

def filter_accounts(accounts, config=None):
    cfg = (config or {}).get("codex", {}) if isinstance(config, dict) else {}
    ignored_accounts = cfg.get("ignored_accounts") or []
    return {
        name: home
        for name, home in accounts.items()
        if not account_is_ignored(name, home, ignored_accounts)
    }

def find_latest_session(codex_home):
    files = list(Path(codex_home).joinpath("sessions").rglob("rollout-*.jsonl"))
    return str(max(files, key=lambda f: f.stat().st_mtime)) if files else None

def get_session_files_since(codex_home, since_timestamp):
    pattern = os.path.join(codex_home, "sessions", "**", "rollout-*.jsonl")
    files = glob.glob(pattern, recursive=True)
    return [f for f in files if os.path.getmtime(f) >= since_timestamp]

def parse_session_tokens(session_file):
    tokens = None
    try:
        with open(session_file, encoding="utf-8") as f:
            for line in f:
                if '"token_count"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("type") == "event_msg":
                        payload = rec.get("payload") or {}
                        if payload.get("type") == "token_count":
                            info = payload.get("info") or {}
                            total = info.get("total_token_usage")
                            if total:
                                tokens = total
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return tokens

def window_key_and_label(window_minutes):
    if window_minutes == 300:
        return "5h", "5h window"
    if window_minutes == 10080:
        return "weekly", "weekly"
    if not window_minutes:
        return "unknown", "unknown"
    if window_minutes % 1440 == 0:
        days = window_minutes // 1440
        return f"{window_minutes}m", f"{days}d window"
    if window_minutes % 60 == 0:
        hours = window_minutes // 60
        return f"{window_minutes}m", f"{hours}h window"
    return f"{window_minutes}m", f"{window_minutes}m window"

def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def normalize_legacy_limit(key, payload):
    window_minutes = 300 if key == "5h" else 10080 if key == "weekly" else None
    _, label = window_key_and_label(window_minutes)
    return {
        "key": key,
        "label": label,
        "window_minutes": window_minutes,
        "used_percent": safe_float(payload.get("used_percent"), 0.0),
        "reset_time": payload.get("reset_time"),
    }

def normalize_rate_limit(payload):
    if not isinstance(payload, dict):
        return None
    window_minutes = payload.get("window_minutes")
    key, label = window_key_and_label(window_minutes)
    return {
        "key": key,
        "label": label,
        "window_minutes": window_minutes,
        "used_percent": safe_float(payload.get("used_percent"), 0.0),
        "reset_time": payload.get("resets_at"),
    }

def parse_usage_limit_message(message):
    if not message:
        return "usage limit reached"
    match = re.search(r"try again at (.+?)(?:\.)?$", message)
    if not match:
        return "usage limit reached"
    reset_text = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", match.group(1))
    try:
        dt_local = datetime.strptime(reset_text, "%b %d, %Y %I:%M %p").replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
        return f"usage limited, {fmt_reset(dt_local)}"
    except ValueError:
        return f"usage limited until {match.group(1)}"

def parse_request_error_message(message):
    if not message:
        return None
    invalid_request = re.search(r'"message":"([^"]+)"', message)
    if invalid_request:
        return invalid_request.group(1).replace('\\"', '"')
    return None

def find_log_issue_in_sqlite(codex_home):
    db_path = os.path.join(codex_home, "logs_2.sqlite")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            row = conn.execute(
                """
                SELECT feedback_log_body
                FROM logs
                WHERE feedback_log_body LIKE '%usage_limit_exceeded%'
                   OR feedback_log_body LIKE '%try again at%'
                   OR feedback_log_body LIKE '%Upgrade to Plus%'
                   OR feedback_log_body LIKE '%invalid_request_error%'
                   OR feedback_log_body LIKE '%not supported%'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"Database error: {e}"

    if not row or not row[0]:
        return None

    issue = parse_usage_limit_message(row[0])
    if issue != "usage limit reached":
        return issue

    request_error = parse_request_error_message(row[0])
    if request_error:
        return request_error

    return None

def find_log_issue_in_text(codex_home):
    log_path = os.path.join(codex_home, "log", "codex-tui.log")
    if not os.path.exists(log_path):
        return None

    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except OSError as e:
        return f"Log read error: {e}"

    for line in reversed(lines[-500:]):
        if "usage_limit_exceeded" in line or "try again at" in line or "Upgrade to Plus" in line:
            issue = parse_usage_limit_message(line)
            if issue != "usage limit reached":
                return issue
        if "invalid_request_error" in line or "not supported" in line:
            request_error = parse_request_error_message(line)
            if request_error:
                return request_error

    return None

def find_log_issue(codex_home):
    return find_log_issue_in_sqlite(codex_home) or find_log_issue_in_text(codex_home)


def parse_limits(session_file):
    limits = {}
    status = None
    tokens = None  # latest cumulative total_token_usage in this session
    with open(session_file, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            if "limit_5h" in rec:
                limits["5h"] = normalize_legacy_limit("5h", rec["limit_5h"])
            if "limit_weekly" in rec:
                limits["weekly"] = normalize_legacy_limit("weekly", rec["limit_weekly"])

            if rec.get("type") != "event_msg":
                continue

            payload = rec.get("payload") or {}
            if payload.get("type") == "error" and payload.get("codex_error_info") == "usage_limit_exceeded":
                status = parse_usage_limit_message(payload.get("message"))

            if payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            total = info.get("total_token_usage")
            if total:
                tokens = total

            rate_limits = payload.get("rate_limits") or {}
            for name in ("primary", "secondary"):
                limit = normalize_rate_limit(rate_limits.get(name))
                if limit:
                    limits[limit["key"]] = limit
    return limits, status, tokens

def get_session_mtime(session_file):
    try:
        return os.path.getmtime(session_file)
    except OSError:
        return None

def get_codex_data(args, config=None):
    data = []
    discovered_accounts = discover_accounts()
    if not discovered_accounts:
        return {"error": "no codex accounts found (~/.codex-*)"}
    accounts = filter_accounts(discovered_accounts, config)
    if not accounts:
        return {"error": "all codex accounts are ignored by config"}

    for name, home in accounts.items():
        acc_data = {
            "name": name,
            "home": redact_path(home) if args.redact else home,
        }

        if not os.path.exists(home):
            acc_data["error"] = "folder not found"
            data.append(acc_data)
            continue

        session = find_latest_session(home)
        if not session:
            issue = find_log_issue(home)
            if issue:
                acc_data["error"] = issue
            else:
                acc_data["error"] = "no sessions yet — run codex once to populate"
            data.append(acc_data)
            continue

        acc_data["session_file"] = redact_path(session) if args.redact else session
        session_mtime = get_session_mtime(session)
        if session_mtime is not None:
            acc_data["last_updated"] = datetime.fromtimestamp(session_mtime, timezone.utc).isoformat()

        limits, status, current_tokens = parse_limits(session)
        
        weekly_reset = None
        for d in limits.values():
            if d.get("key") == "weekly" and d.get("reset_time"):
                weekly_reset = d["reset_time"]
                break
                
        weekly_tokens = None
        if weekly_reset:
            try:
                dt = parse_to_utc(weekly_reset)
                week_start = (dt - timedelta(days=7)).timestamp()
                session_files = get_session_files_since(home, week_start)
                
                weekly_tokens = {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 0,
                }
                
                for sfile in session_files:
                    stokens = parse_session_tokens(sfile)
                    if stokens:
                        for k in weekly_tokens:
                            weekly_tokens[k] += stokens.get(k, 0)
            except OSError:
                pass

        if weekly_tokens and sum(weekly_tokens.values()) > 0:
            acc_data["tokens"] = weekly_tokens
            acc_data["tokens_label"] = "since weekly reset"
        elif current_tokens:
            acc_data["tokens"] = current_tokens
            acc_data["tokens_label"] = "current session"
            
        if not limits:
            issue = status or find_log_issue(home)
            if issue:
                acc_data["error"] = issue
            else:
                acc_data["error"] = "no rate limit data in sessions yet"
            data.append(acc_data)
            continue

        # Compute session age for staleness detection
        session_age_minutes = None
        if session_mtime is not None:
            age_seconds = (datetime.now(timezone.utc) - datetime.fromtimestamp(session_mtime, timezone.utc)).total_seconds()
            session_age_minutes = max(0, age_seconds / 60)

        disp_cfg = load_display_config()
        acc_data["limits"] = []
        for d in sorted(limits.values(), key=lambda item: item.get("window_minutes") or 10**9):
            used_pct = d.get("used_percent", 0)
            left_pct = 100.0 - used_pct
            rst = d.get("reset_time")
            is_stale = False

            # Case 1: reset_time has already passed → limits are reset
            if is_reset_passed(rst):
                left_pct = 100.0
                used_pct = 0.0
                is_stale = True

            # Case 2: session file is older than the rate-limit window →
            # the data has definitely rolled over, regardless of reset_time
            window_minutes = d.get("window_minutes")
            if not is_stale and session_age_minutes and window_minutes:
                if session_age_minutes > window_minutes:
                    left_pct = 100.0
                    used_pct = 0.0
                    is_stale = True

            # When stale, override the reset text directly — fmt_reset's
            # is_stale param only works when reset_time is already past,
            # not when we detected staleness via session age.
            if is_stale:
                rst_fmt = "likely reset (stale data)"
            else:
                rst_fmt = fmt_reset(rst)

            visible = True
            if disp_cfg["auto_hide_enabled"]:
                days_match = re.search(r'(\d+)\s+days?', rst_fmt)
                if days_match and int(days_match.group(1)) > disp_cfg["auto_hide_days"] and left_pct < 10.0:
                    visible = False

            acc_data["limits"].append({
                "label": d["label"],
                "used_percent": used_pct,
                "left_percent": left_pct,
                "reset_time": rst,
                "reset_time_fmt": rst_fmt,
                "is_stale": is_stale,
                "visible": visible,
            })
        data.append(acc_data)

    return {"accounts": data}

def display_codex_text(data, args):
    if "error" in data:
        if data["error"] == "all codex accounts are ignored by config" and args.tool != "codex" and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            return
        print_c(f"\n  Codex", "\033[1;36m", args.no_color)
        print_c(f"    ⚠ {data['error']}", "\033[33m", args.no_color)
        return

    print_c(f"\n  Codex", "\033[1;36m", args.no_color)

    for acc in data.get("accounts", []):
        visible_limits = []
        for lim in acc.get("limits", []):
            if not lim.get("visible", True) and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
                continue
            visible_limits.append(lim)
            
        if not visible_limits and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue

        label = "codex" if acc['name'] == 'default' else f"codex-{acc['name']}"
        if args.no_color:
            print(f"\n  {label}  {acc['home']}")
        else:
            print(f"\n  \033[1m{label}\033[0m  \033[90m{acc['home']}\033[0m")

        if "error" in acc:
            if "not found" in acc["error"] or "✖" in acc["error"]:
                print_c(f"    ✖ {acc['error'].replace('✖ ', '')}", "\033[31m", args.no_color)
            else:
                print_c(f"    ⚠ {acc['error'].replace('⚠ ', '')}", "\033[33m", args.no_color)
            continue

        last_updated = acc.get("last_updated")
        if last_updated and getattr(args, "verbose", False):
            try:
                updated_dt = parse_to_utc(last_updated)
                print_c(
                    f"    last updated {format_timestamp(updated_dt)}",
                    "\033[90m",
                    args.no_color,
                )
            except ValueError:
                pass

        for lim in visible_limits:
            pct = lim["used_percent"]
            left = lim["left_percent"]
            rst = lim["reset_time_fmt"]
            b = bar(pct, no_color=args.no_color)
            stale_hint = "  ⟲" if lim.get("is_stale") else ""
            if args.no_color:
                print(f"    {lim['label']:<9} {b}  {left:5.1f}% left  {rst}{stale_hint}")
            else:
                stale_color = "\033[33m" if lim.get("is_stale") else "\033[90m"
                print(f"    {lim['label']:<9} {b}  {left:5.1f}% left  {stale_color}{rst}{stale_hint}\033[0m")

        tokens = acc.get("tokens")
        if tokens:
            label = acc.get("tokens_label", "current session")
            line = (
                f"    tokens ({label}): "
                f"in {_fmt_tokens(tokens.get('input_tokens', 0))} "
                f"(cached {_fmt_tokens(tokens.get('cached_input_tokens', 0))})  "
                f"out {_fmt_tokens(tokens.get('output_tokens', 0))}  "
                f"reasoning {_fmt_tokens(tokens.get('reasoning_output_tokens', 0))}  "
                f"total {_fmt_tokens(tokens.get('total_tokens', 0))}"
            )
            print_c(line, "\033[90m", args.no_color)


# ── Refresh helpers ─────────────────────────────────────────────────────────

def refresh_account(codex_home, timeout=30):
    """Run a minimal codex exec to trigger fresh rate-limit data in a new session."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return False, "codex not found on PATH"
    env = os.environ.copy()
    env["CODEX_HOME"] = codex_home
    try:
        subprocess.run(
            [codex_bin, "exec", "respond with ok",
             "--skip-git-repo-check", "-s", "read-only"],
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=os.path.expanduser("~"),
        )
        return True, None
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as e:
        return False, str(e)


def _refresh_accounts_parallel(accounts, timeout=30):
    """Refresh a dict of {name: home_dir} accounts in parallel. Returns results."""
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(accounts), 6)) as pool:
        futures = {
            pool.submit(refresh_account, home, timeout): name
            for name, home in accounts.items()
        }
        for fut in as_completed(futures):
            name = futures[fut]
            ok, err = fut.result()
            results[name] = {"ok": ok, "error": err}
    return results


def refresh_accounts(names, config=None, timeout=30):
    """Refresh selected Codex accounts by name."""
    all_accounts = filter_accounts(discover_accounts(), config)
    accounts = {name: all_accounts[name] for name in names if name in all_accounts}
    if not accounts:
        return {}
    return _refresh_accounts_parallel(accounts, timeout)


def refresh_all_accounts(config=None, timeout=30):
    """Refresh all discovered codex accounts."""
    accounts = filter_accounts(discover_accounts(), config)
    if not accounts:
        return {}
    return _refresh_accounts_parallel(accounts, timeout)
