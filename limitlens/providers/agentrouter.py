"""AgentRouter provider — quota tracking for Kilo Code usage."""

import json
import os
import urllib.error
import urllib.request

from limitlens.core import (
    bar,
    identity_line,
    load_display_config,
    load_limitlens_config,
    print_c,
    print_error,
    redact_email,
    section,
)


DEFAULT_QUOTA_URL = "https://agentrouter.org/api/user/self"


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _format_units(value):
    value = float(value or 0)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K"
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _manual_payload(cfg):
    manual = cfg.get("manual") if isinstance(cfg, dict) else None
    if isinstance(manual, dict):
        return manual
    if isinstance(cfg, dict) and any(key in cfg for key in ("quota", "used_quota", "request_count")):
        return cfg
    return None


def _redact_name(value, args):
    if not value:
        return value
    value = str(value)
    if getattr(args, "redact", True) and "@" in value:
        return redact_email(value)
    return value


def parse_agentrouter_quota(payload, args, cfg=None):
    cfg = cfg or {}
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}

    if payload.get("success") is False:
        return {"error": payload.get("message") or "AgentRouter API request failed"}

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return {"error": "Unexpected response format"}

    quota = max(0.0, _number(data.get("quota"), 0.0))
    used = max(0.0, _number(data.get("used_quota"), 0.0))
    remaining = max(0.0, quota - used) if quota > 0 else 0.0
    request_count = max(0, _int(data.get("request_count"), 0))
    pct_left = (remaining / quota * 100.0) if quota > 0 else 0.0
    pct_used = (used / quota * 100.0) if quota > 0 else 0.0

    unit = str(data.get("unit") or cfg.get("unit_label") or "units")
    display_name = _redact_name(data.get("display_name"), args)
    username = _redact_name(data.get("username"), args)
    label = str(data.get("group") or cfg.get("group") or "default")

    info = {
        "username": username,
        "display_name": display_name,
        "group": label,
        "request_count": request_count,
        "last_login_time": data.get("last_login_time"),
        "unit": unit,
        "tiers": [],
    }

    if quota > 0 or used > 0:
        avg = used / request_count if request_count > 0 else 0.0
        info["tiers"].append({
            "label": "AgentRouter quota",
            "remaining": remaining,
            "total": quota,
            "used": used,
            "pct_left": pct_left,
            "pct_used": pct_used,
            "unit": unit,
            "avg_per_request": avg,
        })

    disp_cfg = load_display_config()
    for tier in info["tiers"]:
        tier["visible"] = True
        if disp_cfg["auto_hide_enabled"] and tier["pct_left"] < 5.0:
            tier["visible"] = False

    return info


def _auth_headers_from_env():
    headers = {}
    authorization = os.environ.get("AGENTROUTER_AUTHORIZATION")
    token = os.environ.get("AGENTROUTER_API_TOKEN")
    cookie = os.environ.get("AGENTROUTER_COOKIE")
    user_id = os.environ.get("AGENTROUTER_NEW_API_USER")

    if authorization:
        headers["authorization"] = authorization
    elif token:
        headers["authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    if cookie:
        headers["cookie"] = cookie
    if user_id:
        headers["New-API-User"] = user_id
    return headers


def get_agentrouter_data(args):
    cfg = (load_limitlens_config().get("agentrouter") or {})
    manual = _manual_payload(cfg)
    headers = _auth_headers_from_env()

    if not headers:
        if manual:
            return parse_agentrouter_quota({"data": manual, "success": True}, args, cfg)
        return {"error": "AGENTROUTER_API_TOKEN or AGENTROUTER_COOKIE not set"}

    url = os.environ.get("AGENTROUTER_QUOTA_URL") or cfg.get("quota_url") or DEFAULT_QUOTA_URL
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Cache-Control", "no-store")
    req.add_header("Referer", "https://agentrouter.org/console")
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        if manual:
            return parse_agentrouter_quota({"data": manual, "success": True}, args, cfg)
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        if manual:
            return parse_agentrouter_quota({"data": manual, "success": True}, args, cfg)
        return {"error": f"Request failed: {e}"}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON response from AgentRouter API"}

    return parse_agentrouter_quota(parsed, args, cfg)


def display_agentrouter_text(data, args):
    if "error" in data:
        section("AgentRouter / Kilo Code", args)
        print_error(data["error"], args)
        return

    visible_tiers = []
    for tier in data.get("tiers", []):
        if not tier.get("visible", True) and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue
        visible_tiers.append(tier)

    if not visible_tiers and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    section("AgentRouter / Kilo Code", args)
    identity = data.get("display_name") or data.get("username") or data.get("group") or "signed in"
    identity_line("kilo", identity, args)

    for tier in visible_tiers:
        pct_left = tier.get("pct_left", 0.0)
        pct_used = tier.get("pct_used", 0.0)
        unit = tier.get("unit") or data.get("unit") or "units"
        b = bar(pct_used, no_color=args.no_color)
        used = _format_units(tier.get("used", 0.0))
        total = _format_units(tier.get("total", 0.0))
        remaining = _format_units(tier.get("remaining", 0.0))
        print(f"    quota            {b}  {pct_left:5.1f}% left  {remaining}/{total} {unit}  used {used}")

        request_count = data.get("request_count", 0)
        if request_count:
            avg = _format_units(tier.get("avg_per_request", 0.0))
            print_c(f"    requests         {request_count}  avg {avg} {unit}/request", "\033[90m", args.no_color)

    if data.get("group"):
        print_c(f"    group            {data['group']}", "\033[90m", args.no_color)
