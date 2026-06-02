"""Pioneer provider — queries Pioneer API for billing status."""

import json
import os
import urllib.error
import urllib.request

from limitlens.core import (
    redact_email,
    bar,
    print_c,
    section,
    identity_line,
    print_error,
    is_verbose,
    load_display_config,
    load_limitlens_config,
)


def _float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _has_config_balance(cfg):
    if not isinstance(cfg, dict):
        return False
    keys = (
        "tiers", "credits_remaining", "credits_used", "credits_total", "remaining", "total",
        "free_tier_remaining", "total_usage", "credit_limit",
    )
    return any(key in cfg for key in keys)


def _pioneer_money(value):
    return _float(value, 0.0) / 100.0


def parse_pioneer_billing(data, args):
    if not isinstance(data, dict):
        return {"error": "Unexpected response format"}

    email = data.get("email")
    if getattr(args, "redact", True) and email:
        email = redact_email(email)

    info = {
        "email": email,
        "team_id": data.get("team_id"),
        "team_name": data.get("team_name"),
        "plan": data.get("plan") or data.get("payment_plan"),
        "tiers": [],
        "used_today": _float(data.get("used_today"), 0.0),
        "inferences_today": int(_float(data.get("inferences_today"), 0.0)),
        "unit": data.get("unit") or "$",
        "raw": data,
    }

    tiers_data = data.get("tiers", [])
    if not tiers_data and any(k in data for k in ("free_tier_remaining", "total_usage", "credit_limit")):
        total = _pioneer_money(data.get("credit_limit"))
        remaining = _pioneer_money(data.get("free_tier_remaining"))
        used = _pioneer_money(data.get("total_usage"))
        if total <= 0 and remaining > 0 and used > 0:
            total = remaining + used
        if remaining <= 0 and total > 0 and used > 0:
            remaining = max(0.0, total - used)
        if used <= 0 and total > 0:
            used = max(0.0, total - remaining)
        if total > 0:
            tiers_data = [{
                "label": data.get("team_name") or data.get("payment_plan") or "Pioneer credits",
                "remaining": remaining,
                "total": total,
                "used": used,
                "unit": "$",
            }]

    if not tiers_data and any(k in data for k in ("credits_remaining", "credits_used", "credits_total", "remaining", "total")):
        total = _float(data.get("credits_total") or data.get("total"), 0.0)
        remaining = _float(data.get("credits_remaining") or data.get("remaining"), 0.0)
        used = data.get("credits_used") or data.get("used") or max(0.0, total - remaining)
        used = _float(used, 0.0)
        
        if total <= 0 and remaining > 0:
            total = remaining
            used = 0.0
            
        if total > 0:
            tiers_data = [{
                "label": data.get("plan", "Credits"),
                "remaining": remaining,
                "total": total,
                "used": used,
                "unit": data.get("unit") or "$",
            }]

    for t in tiers_data:
        label = t.get("label", "Credits")
        remaining = _float(t.get("remaining"), 0.0)
        total = _float(t.get("total"), 0.0)
        used = _float(t.get("used"), 0.0)
        if remaining <= 0 and total <= 0 and used <= 0:
            continue

        if total > 0 and used <= 0:
            used = max(0.0, total - remaining)
        if total <= 0 and remaining > 0:
            total = remaining
        
        pct_left = (remaining / total * 100) if total > 0 else 0.0
        pct_used = 100.0 - pct_left
        
        info["tiers"].append({
            "label": label,
            "remaining": remaining,
            "total": total,
            "used": used,
            "unit": t.get("unit") or info["unit"],
            "pct_left": pct_left,
            "pct_used": pct_used
        })

    disp_cfg = load_display_config()
    for tier in info.get("tiers", []):
        pct_left = tier["pct_left"]
        visible = True
        if disp_cfg["auto_hide_enabled"]:
            if pct_left < 10.0:
                visible = False
        tier["visible"] = visible

    return info


def get_pioneer_data(args, config=None):
    if config is None:
        config = load_limitlens_config()
    cfg = config.get("pioneer") or {}
    token = os.environ.get("PIONEER_API_TOKEN")
    if not token:
        if _has_config_balance(cfg):
            return parse_pioneer_billing(cfg, args)
        return {"error": "PIONEER_API_TOKEN environment variable not set"}

    team_id = cfg.get("team_id") or os.environ.get("PIONEER_TEAM_ID")
    if team_id:
        url = f"https://api.pioneer.ai/billing/team/{team_id}/full-status"
    else:
        url = "https://api.pioneer.ai/billing/billing-status"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "*/*")
    req.add_header("authorization", f"Bearer {token}")
    req.add_header("cache-control", "no-cache")
    req.add_header("origin", "https://agent.pioneer.ai")
    req.add_header("pragma", "no-cache")
    req.add_header("referer", "https://agent.pioneer.ai/")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        if _has_config_balance(cfg):
            return parse_pioneer_billing(cfg, args)
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        if _has_config_balance(cfg):
            return parse_pioneer_billing(cfg, args)
        return {"error": f"Request failed: {e}"}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON response from Pioneer API"}

    if not parsed:
        return {"error": "Empty response from Pioneer API"}

    data = parsed.get("data", parsed)
    if isinstance(data, dict):
        data = {**cfg, **data}
    return parse_pioneer_billing(data, args)


def display_pioneer_text(data, args):
    if "error" in data:
        section("Pioneer", args)
        print_error(data["error"], args)
        return

    visible_tiers = []
    for tier in data.get("tiers", []):
        if not tier.get("visible", True) and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
            continue
        visible_tiers.append(tier)

    if not visible_tiers and not (getattr(args, "verbose", False) or getattr(args, "all", False)):
        return

    section("Pioneer", args)
    display_email = data.get("email") or "signed in"
    identity_line("pioneer", display_email, args)

    if not visible_tiers:
        if is_verbose(args):
            print_c("    (no active credit tiers)", "\033[90m", args.no_color)
        return

    for tier in visible_tiers:
        pct_left = tier["pct_left"]
        pct_used = tier["pct_used"]
        label = tier["label"]
        short = label.split(":")[-1].strip() if ":" in label else label
        if len(short) > 15:
            short = short[:15]

        b = bar(pct_used, no_color=args.no_color)
        unit = tier.get("unit") or data.get("unit") or "$"
        if unit == "$":
            amount = f"${tier['remaining']:.2f}/${tier['total']:.2f}"
            used = f"${tier.get('used', 0.0):.2f} used"
        else:
            amount = f"{tier['remaining']:.2f}/{tier['total']:.2f} {unit}"
            used = f"{tier.get('used', 0.0):.2f} {unit} used"
        if args.no_color:
            print(f"    {short:<16} {b}  {pct_left:5.1f}% left  {amount}  {used}")
        else:
            print(f"    {short:<16} {b}  {pct_left:5.1f}% left  \033[90m{amount}  {used}\033[0m")

    if data.get("used_today") or data.get("inferences_today"):
        used_today = _float(data.get("used_today"), 0.0)
        inferences = int(_float(data.get("inferences_today"), 0.0))
        if used_today and inferences:
            print_c(f"    today           ${used_today:.2f} used on {inferences} inferences", "\033[90m", args.no_color)
        elif used_today:
            print_c(f"    today           ${used_today:.2f} used", "\033[90m", args.no_color)
        else:
            print_c(f"    today           {inferences} inferences", "\033[90m", args.no_color)
