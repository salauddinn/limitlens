"""Command Code provider — reads credit balance from the web billing API."""

import json
import os
import urllib.error
import urllib.request

from limitlens.core import bar, identity_line, load_limitlens_config, print_c, print_error, section


DEFAULT_CREDITS_URL = "https://api.commandcode.ai/internal/billing/credits?"


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_commandcode_credits(payload, args=None, cfg=None):
    cfg = cfg or {}
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}

    data = payload.get("credits", payload)
    if not isinstance(data, dict):
        return {"error": "Unexpected response format"}

    credits = {
        "monthly": max(0.0, _number(data.get("monthlyCredits"), 0.0)),
        "purchased": max(0.0, _number(data.get("purchasedCredits"), 0.0)),
        "premium_monthly": max(0.0, _number(data.get("premiumMonthlyCredits"), 0.0)),
        "opensource_monthly": max(0.0, _number(data.get("opensourceMonthlyCredits"), 0.0)),
        "threshold": max(0.0, _number(data.get("creditThreshold"), 0.0)),
        "below_threshold": bool(data.get("belowThreshold", False)),
    }
    available = credits["monthly"] + credits["purchased"] + credits["premium_monthly"] + credits["opensource_monthly"]
    total = max(available, _number(cfg.get("total"), 0.0))
    pct_left = (available / total * 100.0) if total > 0 else 0.0

    tiers = []
    if total > 0:
        tiers.append({
            "label": "credits",
            "remaining": available,
            "total": total,
            "used": max(0.0, total - available),
            "pct_left": pct_left,
            "pct_used": 100.0 - pct_left,
            "unit": "credits",
        })

    return {
        "name": cfg.get("name") or "Command Code",
        "command": cfg.get("command") or "cmd",
        "credits": credits,
        "available": available,
        "tiers": tiers,
    }


def _auth_headers_from_env():
    headers = {}
    authorization = os.environ.get("COMMANDCODE_AUTHORIZATION")
    token = os.environ.get("COMMANDCODE_API_TOKEN")
    cookie = os.environ.get("COMMANDCODE_COOKIE")

    if authorization:
        headers["authorization"] = authorization
    elif token:
        headers["authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    if cookie:
        headers["cookie"] = cookie
    return headers


def _manual_payload(cfg):
    manual = cfg.get("manual") if isinstance(cfg, dict) else None
    if isinstance(manual, dict):
        return manual
    if isinstance(cfg, dict) and any(key in cfg for key in ("credits", "purchasedCredits", "monthlyCredits")):
        return cfg
    return None


def get_commandcode_data(args, config=None):
    if config is None:
        config = load_limitlens_config()
    cfg = config.get("commandcode") or {}
    manual = _manual_payload(cfg)
    headers = _auth_headers_from_env()

    if not headers:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": "COMMANDCODE_COOKIE or COMMANDCODE_AUTHORIZATION not set"}

    url = os.environ.get("COMMANDCODE_CREDITS_URL") or cfg.get("credits_url") or DEFAULT_CREDITS_URL
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("Origin", "https://commandcode.ai")
    req.add_header("Referer", "https://commandcode.ai/")
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": f"Request failed: {e}"}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON response from Command Code API"}

    return parse_commandcode_credits(parsed, args, cfg)


def display_commandcode_text(data, args):
    if "error" in data:
        section("Command Code", args)
        print_error(data["error"], args)
        return

    visible_tiers = data.get("tiers") or []
    if not visible_tiers and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    section("Command Code", args)
    identity_line("cmd", data.get("name") or "Command Code", args)

    for tier in visible_tiers:
        pct_left = tier.get("pct_left", 0.0)
        pct_used = tier.get("pct_used", 0.0)
        b = bar(pct_used, no_color=args.no_color)
        print(f"    credits          {b}  {pct_left:5.1f}% left  {tier['remaining']:.4f}/{tier['total']:.4f} credits")

    credits = data.get("credits") or {}
    for key, label in (
        ("purchased", "purchased"),
        ("monthly", "monthly"),
        ("premium_monthly", "premium monthly"),
        ("opensource_monthly", "opensource monthly"),
    ):
        value = credits.get(key, 0.0)
        if value:
            print_c(f"    {label:<16} {value:.4f} credits", "\033[90m", args.no_color)
    if credits.get("below_threshold"):
        print_c(f"    threshold        below {credits.get('threshold', 0.0):.4f}", "\033[33m", args.no_color)
