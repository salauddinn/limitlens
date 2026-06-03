"""Observed usage provider — OpenCode SQLite + Copilot CLI OTel spend tracking."""

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from limitlens.core import (
    redact_path,
    parse_to_utc,
    configured_days,
    print_c,
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
        row = {
            "provider": provider,
            "model": model,
            "requests": totals["requests"],
            "cost": totals["cost"],
            "tokens": totals["tokens"],
        }
        if totals.get("parent"):
            row["parent"] = totals["parent"]
        rows.append(row)
    rows.sort(key=lambda r: (-r["tokens"].get("total", 0), r["provider"], r["model"]))
    return rows

def float_value(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def first_present(mapping, keys):
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None

def normalize_credit_limit(raw, fallback_name="credits", default_unit="credits"):
    if not isinstance(raw, dict):
        return None

    name = str(raw.get("name") or raw.get("label") or raw.get("tier") or fallback_name)
    unit = str(raw.get("unit") or raw.get("currency") or default_unit or "credits")
    raw_total = first_present(raw, ("credits_total", "total", "limit"))
    raw_remaining = first_present(raw, ("credits_remaining", "remaining", "left"))
    raw_used = first_present(raw, ("credits_used", "used", "spent"))
    total = float_value(raw_total, 0.0)
    remaining = float_value(raw_remaining, 0.0)
    used = float_value(raw_used, 0.0)

    if total <= 0 and remaining > 0 and used > 0:
        total = remaining + used
    elif total <= 0 and remaining > 0:
        total = remaining
    elif total > 0 and raw_remaining is None and raw_used is None:
        remaining = total
    elif total > 0 and raw_remaining is None and used > 0:
        remaining = max(0.0, total - used)
    elif total > 0 and raw_used is None and raw_remaining is not None:
        used = max(0.0, total - remaining)

    if total <= 0 and remaining <= 0 and used <= 0:
        return None

    pct_left = (remaining / total * 100) if total > 0 else 0.0
    return {
        "name": name,
        "unit": unit,
        "total": total,
        "remaining": remaining,
        "used": used,
        "pct_left": pct_left,
    }

def get_opencode_credit_limits(cfg):
    default_unit = cfg.get("credit_unit") or cfg.get("unit") or "credits"
    raw_limits = first_present(cfg, ("credit_limits", "credits", "limits", "tiers"))
    limits = []

    if isinstance(raw_limits, list):
        for idx, raw in enumerate(raw_limits, start=1):
            item = normalize_credit_limit(raw, fallback_name=f"credits {idx}", default_unit=default_unit)
            if item:
                limits.append(item)
    elif isinstance(raw_limits, dict):
        value_keys = {"credits_total", "total", "limit", "credits_remaining", "remaining", "left", "credits_used", "used", "spent"}
        if any(key in raw_limits for key in value_keys):
            item = normalize_credit_limit(raw_limits, default_unit=default_unit)
            if item:
                limits.append(item)
        else:
            for name, raw in raw_limits.items():
                if isinstance(raw, dict):
                    payload = dict(raw)
                    payload.setdefault("name", name)
                else:
                    payload = {"name": name, "remaining": raw}
                item = normalize_credit_limit(payload, fallback_name=name, default_unit=default_unit)
                if item:
                    limits.append(item)

    if not limits and any(key in cfg for key in ("credits_total", "credits_remaining", "credits_used")):
        item = normalize_credit_limit(cfg, default_unit=default_unit)
        if item:
            limits.append(item)

    limits.sort(key=lambda item: (item["pct_left"], item["name"]))
    return limits

def model_is_ignored(provider, model, ignored_models):
    if isinstance(ignored_models, str):
        ignored_models = [ignored_models]
    elif not isinstance(ignored_models, list):
        return False

    provider_model = f"{provider}/{model}".lower()
    model = str(model).lower()
    for ignored in ignored_models:
        value = str(ignored).strip().lower()
        if not value:
            continue
        if value == provider_model or value == model:
            return True
    return False

def model_parent_label(provider, model, parents):
    if not isinstance(parents, dict):
        return None

    provider = str(provider)
    model = str(model)
    candidates = (
        f"{provider}/{model}".lower(),
        f"{provider}/*".lower(),
        model.lower(),
    )
    normalized = {str(key).strip().lower(): value for key, value in parents.items()}
    for key in candidates:
        value = normalized.get(key)
        if value:
            return str(value)
    return None

def get_opencode_usage(config):
    cfg = config.get("opencode", {})
    if not cfg.get("enabled", True):
        return {"disabled": True}
    db_path = os.path.expanduser(cfg.get("db_path") or "~/.local/share/opencode/opencode.db")
    if not os.path.exists(db_path):
        return {"error": f"OpenCode DB not found: {redact_path(db_path)}"}

    providers = set(cfg.get("providers") or [])
    ignored_models = cfg.get("ignored_models") or []
    model_parents = cfg.get("model_parents") or cfg.get("parents") or {}
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
        if model_is_ignored(provider, model, ignored_models):
            continue
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
            parent = model_parent_label(provider, model, model_parents)
            if parent:
                totals["parent"] = parent
            add_usage(totals, cost=data.get("cost"), tokens=tokens)

    return {
        "db_path": redact_path(db_path),
        "credit_limits": get_opencode_credit_limits(cfg),
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
        try:
            if number > 1e18:
                return datetime.fromtimestamp(number / 1e9, timezone.utc)
            if number > 1e12:
                return datetime.fromtimestamp(number / 1000, timezone.utc)
            return datetime.fromtimestamp(number, timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        # Try to parse as integer or float first if it looks numeric and is not ISO date format
        if not any(char in value for char in ("-", ":", "T", "Z")):
            try:
                return parse_otel_timestamp(float(value))
            except ValueError:
                pass
        try:
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

def pi_usage_cost(usage):
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if isinstance(cost, dict):
        return cost.get("total") or 0
    return cost or 0

def pi_usage_tokens(usage):
    if not isinstance(usage, dict):
        return {}
    input_t = usage.get("input") or 0
    output_t = usage.get("output") or 0
    total_t = usage.get("totalTokens") or usage.get("total") or 0
    if total_t <= 0:
        total_t = input_t + output_t
    return {
        "total": total_t,
        "input": input_t,
        "output": output_t,
        "reasoning": usage.get("reasoningTokens") or usage.get("reasoning") or 0,
        "cache_read": usage.get("cacheRead") or usage.get("cache_read") or 0,
        "cache_write": usage.get("cacheWrite") or usage.get("cache_write") or 0,
    }

def get_pi_usage(config):
    cfg = config.get("pi", {})
    if not cfg.get("enabled", True):
        return {"disabled": True}
    sessions_dir = os.path.expanduser(cfg.get("sessions_dir") or "~/.pi/agent/sessions")
    root = Path(sessions_dir)
    if not root.exists():
        return {"error": f"Pi sessions dir not found: {redact_path(sessions_dir)}"}

    providers = set(cfg.get("providers") or [])
    ignored_models = cfg.get("ignored_models") or []
    model_parents = cfg.get("model_parents") or cfg.get("parents") or {}
    days_list = configured_days(cfg)
    windows = {
        str(days): {"days": days, "since": usage_window_start(days), "by_key": {}}
        for days in days_list
    }
    min_since_ts = min(win["since"].timestamp() for win in windows.values())

    files = []
    try:
        for path in root.rglob("*.jsonl"):
            try:
                if path.stat().st_mtime >= min_since_ts:
                    files.append(path)
            except OSError:
                continue
    except OSError as e:
        return {"error": f"Pi sessions read error: {e}"}

    for path in files:
        try:
            f = path.open(encoding="utf-8")
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "message":
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage") or {}
                tokens = pi_usage_tokens(usage)
                if token_total_value(tokens) <= 0 and not pi_usage_cost(usage):
                    continue
                ts = parse_otel_timestamp(msg.get("timestamp")) or parse_otel_timestamp(rec.get("timestamp"))
                if ts is None:
                    continue
                provider = msg.get("provider") or msg.get("api") or "unknown"
                if providers and provider not in providers:
                    continue
                model = msg.get("model") or "unknown"
                if model_is_ignored(provider, model, ignored_models):
                    continue
                for win in windows.values():
                    if ts < win["since"]:
                        continue
                    key = (provider, model)
                    totals = win["by_key"].setdefault(key, empty_usage_totals())
                    parent = model_parent_label(provider, model, model_parents)
                    if parent:
                        totals["parent"] = parent
                    add_usage(totals, cost=pi_usage_cost(usage), tokens=tokens)

    return {
        "sessions_dir": redact_path(sessions_dir),
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
        "pi": get_pi_usage(config),
        "copilot_cli": get_copilot_cli_usage(config),
    }

def get_pi_data(args, config):
    return get_pi_usage(config)

def display_pi_text(data, args):
    display_opencode_text({"pi": data}, args)

def display_usage_rows(rows, args):
    if not rows:
        print_c("      no usage", "\033[90m", getattr(args, 'no_color', False))
        return

    max_rows = len(rows) if getattr(args, "verbose", False) else 3
    shown = 0
    grouped = {}
    for row in rows:
        grouped.setdefault(row["provider"], []).append(row)

    for provider, provider_rows in grouped.items():
        if shown >= max_rows:
            break
        print_c(f"      {provider}", "\033[90m", getattr(args, 'no_color', False))
        for row in provider_rows:
            if shown >= max_rows:
                break
            shown += 1
            toks = row.get("tokens") or {}
            cost = row.get("cost", 0.0)
            cost_text = f"  ${cost:.2f}" if cost else ""
            parent_text = f"  parent: {row['parent']}" if row.get("parent") else ""
            print(
                f"        {row['model']:<30} "
                f"{_fmt_tokens(toks.get('total', 0)):>7} tokens  "
                f"{row['requests']:>4} req"
                f"{cost_text}"
                f"{parent_text}"
            )

    hidden = len(rows) - shown
    if hidden > 0:
        print_c(f"      +{hidden} more (use --verbose)", "\033[90m", getattr(args, 'no_color', False))

def display_usage_rows_detailed(rows, args):
    if not rows:
        print_c("      no usage", "\033[90m", getattr(args, 'no_color', False))
        return
    for row in rows:
        toks = row.get("tokens") or {}
        cost = row.get("cost", 0.0)
        cost_text = f"  ${cost:.4f}" if cost else ""
        parent_text = f"  parent: {row['parent']}" if row.get("parent") else ""
        print(
            f"      {row['provider']}/{row['model']:<28} "
            f"{row['requests']:>4} req  "
            f"{_fmt_tokens(toks.get('total', 0)):>7} tokens  "
            f"in {_fmt_tokens(toks.get('input', 0))}  "
            f"out {_fmt_tokens(toks.get('output', 0))}"
            f"{cost_text}"
            f"{parent_text}"
        )

def format_credit_amount(value, unit):
    normalized = (unit or "credits").lower()
    if normalized in ("$", "usd", "dollars"):
        return f"${value:.2f}"
    if normalized in ("₹", "inr", "rupees"):
        return f"₹{value:.2f}"
    suffix = "" if normalized in ("", "none") else f" {unit}"
    return f"{value:.2f}{suffix}"

def format_credit_pair(remaining, total, unit):
    normalized = (unit or "credits").lower()
    if normalized in ("$", "usd", "dollars"):
        return f"${remaining:.2f}/${total:.2f}"
    if normalized in ("₹", "inr", "rupees"):
        return f"₹{remaining:.2f}/₹{total:.2f}"
    suffix = "" if normalized in ("", "none") else f" {unit}"
    return f"{remaining:.2f}/{total:.2f}{suffix}"

def display_credit_limits(limits, args):
    if not limits:
        return
    print_c("    credits", "\033[90m", getattr(args, 'no_color', False))
    for limit in limits:
        unit = limit.get("unit") or "credits"
        remaining = float_value(limit.get("remaining"), 0.0)
        total = float_value(limit.get("total"), 0.0)
        used = float_value(limit.get("used"), 0.0)
        pct_left = float_value(limit.get("pct_left"), 0.0)
        pair = format_credit_pair(remaining, total, unit)
        used_text = format_credit_amount(used, unit)
        print(f"      {limit['name']:<18} {pct_left:5.1f}% left  {pair}  used {used_text}")

def display_usage_source(name, data, args):
    if data.get("disabled"):
        return
    if "error" in data:
        hide_optional_error = name == "copilot-cli" or (name == "pi" and getattr(args, "tool", None) != "pi")
        if hide_optional_error and not getattr(args, "verbose", False):
            return
        print(f"\n  {name}")
        print_c(f"    not configured: {data['error']}", "\033[90m", getattr(args, 'no_color', False))
        if data.get("hint"):
            print_c(f"    {data['hint']}", "\033[90m", getattr(args, 'no_color', False))
        return
    windows = data.get("windows", [])
    credit_limits = data.get("credit_limits") or []
    has_any_data = any(win.get("models") for win in windows)
    if not has_any_data and not credit_limits and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    print(f"\n  {name}")
    display_credit_limits(credit_limits, args)
    shown_windows = windows if (getattr(args, "verbose", False) or getattr(args, "all", False)) else windows[:1]
    for win in shown_windows:
        models = win.get("models") or []
        if not models and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue
        label = "today" if win["days"] == 1 else f"last {win['days']}d"
        print_c(f"    {label}", "\033[90m", getattr(args, 'no_color', False))
        if getattr(args, "verbose", False):
            display_usage_rows_detailed(models, args)
        else:
            display_usage_rows(models, args)

def display_opencode_text(data, args):
    if getattr(args, 'tool', None) not in ("opencode", "pi", "all"):
        return
        
    op = data.get("opencode") or {}
    pi = data.get("pi") or {}
    co = data.get("copilot_cli") or {}
    sources = [("opencode", op), ("pi", pi), ("copilot-cli", co)]
    if getattr(args, 'tool', None) == "pi":
        sources = [("pi", pi)]
    
    if not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        has_data = False
        for name, source in sources:
            has_data = has_data or any(w.get("models") for w in source.get("windows", []))
            has_data = has_data or bool(source.get("credit_limits"))
            has_data = has_data or ("error" in source and (name == "opencode" or getattr(args, 'tool', None) == name))
        if not has_data:
            return

    print_c("\n  Spend / Usage", "\033[1;36m", getattr(args, 'no_color', False))
    for name, source in sources:
        display_usage_source(name, source, args)

def compact_reco_name(name):
    name = name.replace("antigravity:", "ag:")
    name = name.replace("Claude Opus 4.6", "Opus 4.6")
    name = name.replace("Gemini 3.5 Flash", "Flash")
    name = name.replace("Gemini 3.1 Pro", "Gemini Pro")
    return name

def display_at_glance(result, recs, args):
    print_c("\n  At a glance", "\033[1;35m", getattr(args, 'no_color', False))
    labels = (
        ("hard", "hard task"),
        ("quick", "quick edit"),
        ("cli", "cli"),
    )
    for key, label in labels:
        picks = recs.get(key) or []
        if not picks:
            print_c(f"    {label:<10} no usable option", "\033[33m", getattr(args, 'no_color', False))
            continue
        top = picks[0]
        reset = f" · {top['reset_label']}" if top.get("reset_label") else ""
        line = f"    {label:<10} {compact_reco_name(top['name'])} · {top['headroom_pct']:.0f}% left{reset}"
        print_c(line, "\033[32m", getattr(args, 'no_color', False))

    usage_sources = result.get("opencode") or {}
    top_usage = []
    for source_name, usage in usage_sources.items():
        if not isinstance(usage, dict):
            continue
        today = next((w for w in usage.get("windows", []) if w.get("days") == 1), None)
        for row in (today or {}).get("models", []):
            item = dict(row)
            item["source"] = source_name
            top_usage.append(item)
    top_usage.sort(key=lambda r: -((r.get("tokens") or {}).get("total", 0)))
    if top_usage:
        row = top_usage[0]
        toks = (row.get("tokens") or {}).get("total", 0)
        cost = row.get("cost", 0.0)
        cost_text = f" · ${cost:.2f}" if cost else ""
        parent_text = f" · parent: {row['parent']}" if row.get("parent") else ""
        print_c(
            f"    top usage  {row['source']}:{row['provider']}/{row['model']} · {_fmt_tokens(toks)} tokens today{cost_text}{parent_text}",
            "\033[90m",
            getattr(args, 'no_color', False),
        )
