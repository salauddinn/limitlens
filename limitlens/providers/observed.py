"""Observed usage provider — OpenCode SQLite + Copilot CLI OTel spend tracking."""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

from limitlens.core import (
    redact_path,
    parse_to_utc,
    configured_days,
    print_c,
    is_verbose,
    _fmt_tokens,
)

# ── Observed usage helpers ──────────────────────────────────────────────────

def usage_window_start(days):
    now = datetime.now(timezone.utc)
    return now - timedelta(days=days)

def millis_from_dt(dt):
    return int(dt.timestamp() * 1000)

def empty_usage_totals():
    return {
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
    }

def add_usage(totals, cost=0, tokens=None):
    totals["requests"] += 1
    try:
        totals["cost"] += float(cost or 0)
    except (TypeError, ValueError):
        pass
    tokens = tokens or {}
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    field_map = {
        "total": tokens.get("total"),
        "input": tokens.get("input"),
        "output": tokens.get("output"),
        "reasoning": tokens.get("reasoning"),
        "cache_read": cache.get("read") if cache else tokens.get("cache_read"),
        "cache_write": cache.get("write") if cache else tokens.get("cache_write"),
    }
    for key, value in field_map.items():
        try:
            totals["tokens"][key] += int(value or 0)
        except (TypeError, ValueError):
            pass

def token_total_value(tokens):
    if not isinstance(tokens, dict):
        return 0
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    keys = (
        tokens.get("total"),
        tokens.get("input"),
        tokens.get("output"),
        tokens.get("reasoning"),
        cache.get("read") if cache else tokens.get("cache_read"),
        cache.get("write") if cache else tokens.get("cache_write"),
    )
    total = 0
    for value in keys:
        try:
            total += int(value or 0)
        except (TypeError, ValueError):
            pass
    return total

def usage_summary_rows(by_key):
    rows = []
    for (provider, model), totals in by_key.items():
        rows.append({
            "provider": provider,
            "model": model,
            "requests": totals["requests"],
            "cost": totals["cost"],
            "tokens": totals["tokens"],
        })
    rows.sort(key=lambda r: (-r["tokens"].get("total", 0), r["provider"], r["model"]))
    return rows

def get_opencode_usage(config):
    cfg = config.get("opencode", {})
    if not cfg.get("enabled", True):
        return {"disabled": True}
    db_path = os.path.expanduser(cfg.get("db_path") or "~/.local/share/opencode/opencode.db")
    if not os.path.exists(db_path):
        return {"error": f"OpenCode DB not found: {redact_path(db_path)}"}

    providers = set(cfg.get("providers") or [])
    days_list = configured_days(cfg)
    windows = {
        str(days): {"days": days, "since": usage_window_start(days), "by_key": {}}
        for days in days_list
    }
    min_since_ms = min(millis_from_dt(w["since"]) for w in windows.values())

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            rows = conn.execute(
                """
                SELECT data
                FROM message
                WHERE time_created >= ?
                  AND data LIKE '%"tokens"%'
                """,
                (min_since_ms,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"error": f"OpenCode DB error: {e}"}

    for (data_text,) in rows:
        try:
            data = json.loads(data_text)
        except json.JSONDecodeError:
            continue
        if data.get("role") != "assistant":
            continue
        provider = data.get("providerID") or "unknown"
        if providers and provider not in providers:
            continue
        model = data.get("modelID") or "unknown"
        tokens = data.get("tokens") or {}
        if token_total_value(tokens) <= 0 and not data.get("cost"):
            continue
        created = ((data.get("time") or {}).get("created"))
        if created is None:
            continue
        try:
            created_dt = datetime.fromtimestamp(float(created) / 1000.0, timezone.utc)
        except (TypeError, ValueError, OSError):
            continue
        for win in windows.values():
            if created_dt < win["since"]:
                continue
            key = (provider, model)
            totals = win["by_key"].setdefault(key, empty_usage_totals())
            add_usage(totals, cost=data.get("cost"), tokens=tokens)

    return {
        "db_path": redact_path(db_path),
        "windows": [
            {
                "days": win["days"],
                "since": win["since"].isoformat(),
                "models": usage_summary_rows(win["by_key"]),
            }
            for win in windows.values()
        ],
    }

def find_values_by_key(obj, wanted):
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in wanted:
                found.append(value)
            found.extend(find_values_by_key(value, wanted))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_values_by_key(item, wanted))
    return found

def first_number_for_keys(obj, keys):
    for value in find_values_by_key(obj, set(keys)):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0

def first_string_for_keys(obj, keys, default="unknown"):
    for value in find_values_by_key(obj, set(keys)):
        if isinstance(value, str) and value:
            return value
    return default

def parse_otel_timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1e18:
            return datetime.fromtimestamp(number / 1e9, timezone.utc)
        if number > 1e12:
            return datetime.fromtimestamp(number / 1000, timezone.utc)
        return datetime.fromtimestamp(number, timezone.utc)
    if isinstance(value, str):
        try:
            if value.isdigit():
                return parse_otel_timestamp(int(value))
            return parse_to_utc(value)
        except (TypeError, ValueError, OSError):
            return None
    return None

def get_copilot_cli_usage(config):
    cfg = config.get("copilot_cli", {})
    if not cfg.get("enabled", True):
        return {"disabled": True}
    path = os.path.expanduser(cfg.get("otel_jsonl_path") or "~/.cache/limitlens/copilot-otel.jsonl")
    if not os.path.exists(path):
        return {
            "error": f"Copilot OTel file not found: {redact_path(path)}",
            "hint": "run Copilot with COPILOT_OTEL_FILE_EXPORTER_PATH set to this path",
        }

    days_list = configured_days(cfg)
    windows = {
        str(days): {"days": days, "since": usage_window_start(days), "by_key": {}}
        for days in days_list
    }

    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        return {"error": f"Copilot OTel read error: {e}"}

    with f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = (
                parse_otel_timestamp(rec.get("timeUnixNano"))
                or parse_otel_timestamp(rec.get("startTimeUnixNano"))
                or parse_otel_timestamp(rec.get("timestamp"))
                or parse_otel_timestamp(rec.get("time"))
            )
            if ts is None:
                continue
            input_tokens = first_number_for_keys(rec, ("gen_ai.usage.input_tokens", "input_tokens", "input"))
            output_tokens = first_number_for_keys(rec, ("gen_ai.usage.output_tokens", "output_tokens", "output"))
            total_tokens = first_number_for_keys(rec, ("gen_ai.usage.total_tokens", "total_tokens", "total"))
            if total_tokens <= 0:
                total_tokens = input_tokens + output_tokens
            if total_tokens <= 0:
                continue
            provider = first_string_for_keys(rec, ("gen_ai.system", "provider", "providerID"), default="github-copilot")
            model = first_string_for_keys(rec, ("gen_ai.request.model", "gen_ai.response.model", "model", "modelID"), default="unknown")
            tokens = {
                "total": total_tokens,
                "input": input_tokens,
                "output": output_tokens,
            }
            for win in windows.values():
                if ts < win["since"]:
                    continue
                key = (provider, model)
                totals = win["by_key"].setdefault(key, empty_usage_totals())
                add_usage(totals, cost=0, tokens=tokens)

    return {
        "otel_jsonl_path": redact_path(path),
        "windows": [
            {
                "days": win["days"],
                "since": win["since"].isoformat(),
                "models": usage_summary_rows(win["by_key"]),
            }
            for win in windows.values()
        ],
    }

def get_opencode_data(args, config):
    return {
        "opencode": get_opencode_usage(config),
        "copilot_cli": get_copilot_cli_usage(config),
    }

def display_usage_rows(rows, args):
    if not rows:
        print_c("      no usage", "\033[90m", args.no_color)
        return

    max_rows = len(rows) if getattr(args, "verbose", False) else 3
    shown = 0
    grouped = {}
    for row in rows:
        grouped.setdefault(row["provider"], []).append(row)

    for provider, provider_rows in grouped.items():
        if shown >= max_rows:
            break
        print_c(f"      {provider}", "\033[90m", args.no_color)
        for row in provider_rows:
            if shown >= max_rows:
                break
            shown += 1
            toks = row.get("tokens") or {}
            cost = row.get("cost", 0.0)
            cost_text = f"  ${cost:.2f}" if cost else ""
            print(
                f"        {row['model']:<30} "
                f"{_fmt_tokens(toks.get('total', 0)):>7} tokens  "
                f"{row['requests']:>4} req"
                f"{cost_text}"
            )

    hidden = len(rows) - shown
    if hidden > 0:
        print_c(f"      +{hidden} more (use --verbose)", "\033[90m", args.no_color)

def display_usage_rows_detailed(rows, args):
    if not rows:
        print_c("      no usage", "\033[90m", args.no_color)
        return
    for row in rows:
        toks = row.get("tokens") or {}
        cost = row.get("cost", 0.0)
        cost_text = f"  ${cost:.4f}" if cost else ""
        print(
            f"      {row['provider']}/{row['model']:<28} "
            f"{row['requests']:>4} req  "
            f"{_fmt_tokens(toks.get('total', 0)):>7} tokens  "
            f"in {_fmt_tokens(toks.get('input', 0))}  "
            f"out {_fmt_tokens(toks.get('output', 0))}"
            f"{cost_text}"
        )

def display_usage_source(name, data, args):
    if data.get("disabled"):
        return
    if "error" in data:
        if name == "copilot-cli" and not getattr(args, "verbose", False):
            return
        print(f"\n  {name}")
        print_c(f"    not configured: {data['error']}", "\033[90m", args.no_color)
        if data.get("hint"):
            print_c(f"    {data['hint']}", "\033[90m", args.no_color)
        return
    windows = data.get("windows", [])
    has_any_data = any(win.get("models") for win in windows)
    if not has_any_data and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return
        
    print(f"\n  {name}")
    for win in windows:
        models = win.get("models") or []
        if not models and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue
        label = "today" if win["days"] == 1 else f"last {win['days']}d"
        print_c(f"    {label}", "\033[90m", args.no_color)
        if getattr(args, "verbose", False):
            display_usage_rows_detailed(models, args)
        else:
            display_usage_rows(models, args)

def display_opencode_text(data, args):
    if args.tool != "opencode":
        return
        
    op = data.get("opencode") or {}
    co = data.get("copilot_cli") or {}
    
    if not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        op_has_data = any(w.get("models") for w in op.get("windows", [])) or "error" in op
        co_has_data = any(w.get("models") for w in co.get("windows", [])) or ("error" in co and not getattr(args, "verbose", False)) # copilot-cli error is hidden if not verbose
        
        if not op_has_data and not co_has_data:
            return

    print_c(f"\n  Spend / Usage", "\033[1;36m", args.no_color)
    display_usage_source("opencode", op, args)
    display_usage_source("copilot-cli", co, args)

def compact_reco_name(name):
    name = name.replace("antigravity:", "ag:")
    name = name.replace("Claude Opus 4.6", "Opus 4.6")
    name = name.replace("Gemini 3.5 Flash", "Flash")
    name = name.replace("Gemini 3.1 Pro", "Gemini Pro")
    return name

def display_at_glance(result, recs, args):
    print_c("\n  At a glance", "\033[1;35m", args.no_color)
    labels = (
        ("hard", "hard task"),
        ("quick", "quick edit"),
        ("cli", "cli"),
    )
    for key, label in labels:
        picks = recs.get(key) or []
        if not picks:
            print_c(f"    {label:<10} no usable option", "\033[33m", args.no_color)
            continue
        top = picks[0]
        reset = f" · {top['reset_label']}" if top.get("reset_label") else ""
        line = f"    {label:<10} {compact_reco_name(top['name'])} · {top['headroom_pct']:.0f}% left{reset}"
        print_c(line, "\033[32m", args.no_color)

    usage = ((result.get("opencode") or {}).get("opencode") or {})
    today = next((w for w in usage.get("windows", []) if w.get("days") == 1), None)
    top_usage = (today or {}).get("models", [])
    if top_usage:
        row = top_usage[0]
        toks = (row.get("tokens") or {}).get("total", 0)
        cost = row.get("cost", 0.0)
        cost_text = f" · ${cost:.2f}" if cost else ""
        print_c(
            f"    top usage  {row['provider']}/{row['model']} · {_fmt_tokens(toks)} tokens today{cost_text}",
            "\033[90m",
            args.no_color,
        )
