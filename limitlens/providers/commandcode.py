"""Command Code provider — reads quota/credit balance from billing APIs."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from limitlens.core import bar, identity_line, load_limitlens_config, print_c, print_error, section
from ..logging import get_logger

log = get_logger("limitlens.providers.commandcode")


DEFAULT_CREDITS_URL = "https://api.commandcode.ai/internal/billing/credits"
MERLIN_STATUS_HOST = "uam.getmerlin.in"
DEFAULT_MERLIN_ORIGIN = "https://api.commandcode.ai"
DEFAULT_MERLIN_VERSION = "extension-7.5.24"
DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            value = _optional_number(mapping.get(key))
            if value is not None:
                return max(0.0, value)
    return None


def _millis_to_iso(value):
    try:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value) / 1000.0).astimezone().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _merlin_request_timestamp():
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    zone = now.tzinfo.tzname(now) if now.tzinfo else "local"
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}{offset}[{zone}]"


def _is_merlin_status_payload(payload):
    if not isinstance(payload, dict):
        return False
    user = ((payload.get("data") or {}).get("user") or {})
    return bool(user) and ("cappedFeatures" in user or "dailyUsage" in user or "monthlyUsage" in user)


def _parse_merlin_status(payload, cfg=None):
    cfg = cfg or {}
    user = ((payload.get("data") or {}).get("user") or {})
    if not isinstance(user, dict):
        return {"error": "Unexpected response format"}

    capped = user.get("cappedFeatures") or {}
    primary = capped.get("merlin") if isinstance(capped, dict) else None
    if not isinstance(primary, dict):
        primary = {}

    limit = max(_number(primary.get("limit"), _number(user.get("limit"), 0.0)), 0.0)
    used = max(_number(primary.get("used"), _number(user.get("used"), 0.0)), 0.0)
    available = max(0.0, limit - used)
    pct_left = (available / limit * 100.0) if limit > 0 else 0.0
    reset_at = _millis_to_iso(primary.get("resetsAt") or (user.get("dailyUsage") or {}).get("resetsAt"))

    return {
        "name": cfg.get("name") or "Command Code",
        "command": cfg.get("command") or "cmd",
        "plan": user.get("userPlan") or user.get("type"),
        "available": available,
        "unit_label": "uses",
        "tiers": [{
            "label": "merlin",
            "remaining": available,
            "total": limit,
            "used": used,
            "pct_left": pct_left,
            "pct_used": 100.0 - pct_left,
            "unit": "uses",
            "reset_time": reset_at,
        }],
        "daily_usage": user.get("dailyUsage") if isinstance(user.get("dailyUsage"), dict) else None,
        "monthly_usage": user.get("monthlyUsage") if isinstance(user.get("monthlyUsage"), dict) else None,
    }


def parse_commandcode_credits(payload, args=None, cfg=None):
    cfg = cfg or {}
    if not isinstance(payload, dict):
        return {"error": "Unexpected response format"}
    if _is_merlin_status_payload(payload):
        return _parse_merlin_status(payload, cfg)

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
    # Command Code's monthly bucket is the effective monthly pool; premium /
    # opensource monthly values are informational sub-buckets and should not be
    # added again or they will double-count the total available credits.
    available = credits["monthly"] + credits["purchased"]
    used = _first_number(data, (
        "usedCredits",
        "creditsUsed",
        "creditUsed",
        "usageCredits",
        "used",
        "spent",
    ))
    total = _first_number(data, (
        "totalCredits",
        "creditsTotal",
        "creditLimit",
        "credit_limit",
        "limit",
        "total",
    ))
    cfg_total = _optional_number(cfg.get("total"))
    if cfg_total is not None:
        total = max(total or 0.0, cfg_total)
    if total is None and used is not None:
        total = available + used
    elif total is not None:
        total = max(total, available)
        if used is None:
            used = max(0.0, total - available)

    pct_left = (available / total * 100.0) if total and total > 0 else None
    pct_used = 100.0 - pct_left if pct_left is not None else None

    tiers = [{
        "label": "credits",
        "remaining": available,
        "total": total,
        "used": used,
        "pct_left": pct_left,
        "pct_used": pct_used,
        "unit": "credits",
    }]

    return {
        "name": cfg.get("name") or "Command Code",
        "command": cfg.get("command") or "cmd",
        "credits": credits,
        "available": available,
        "unit_label": "credits",
        "tiers": tiers,
    }


def _clean_header_value(value):
    return str(value or "").replace("\r", "").replace("\n", "").strip()


def _auth_headers_from_env():
    headers = {}
    authorization = _clean_header_value(os.environ.get("COMMANDCODE_AUTHORIZATION"))
    token = _clean_header_value(os.environ.get("COMMANDCODE_API_TOKEN"))
    cookie = _clean_header_value(os.environ.get("COMMANDCODE_COOKIE"))

    if not authorization and not token and not cookie:
        try:
            from limitlens.keychain import get_keychain_token
            kc_token = get_keychain_token("commandcode")
            if kc_token:
                if "=" in kc_token and not kc_token.strip().endswith("=") and " " not in kc_token.strip():
                    cookie = kc_token
                else:
                    token = kc_token
        except ImportError:
            pass

    if authorization:
        headers["authorization"] = authorization
    elif token:
        headers["authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    if cookie:
        headers["cookie"] = cookie
    return headers


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def _validated_web_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must use http or https")
    if parsed.hostname not in ("api.commandcode.ai", "uam.getmerlin.in"):
        raise ValueError(f"Untrusted URL domain: {parsed.hostname}. Must be api.commandcode.ai or uam.getmerlin.in")
    return url


def _is_merlin_status_url(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower() == MERLIN_STATUS_HOST
    except Exception:
        log.exception("Error checking if merlin status URL")
        return False


def _open_no_redirect(req, timeout=10):
    opener = urllib.request.build_opener(NoRedirectHandler())
    return opener.open(req, timeout=timeout)


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
    try:
        url = _validated_web_url(url)
    except ValueError as e:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": str(e)}
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("User-Agent", _clean_header_value(os.environ.get("COMMANDCODE_USER_AGENT") or DEFAULT_BROWSER_UA))
    if _is_merlin_status_url(url):
        req.add_header("Accept-Language", _clean_header_value(os.environ.get("COMMANDCODE_ACCEPT_LANGUAGE") or "en-US,en;q=0.7"))
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", _clean_header_value(os.environ.get("COMMANDCODE_ORIGIN") or DEFAULT_MERLIN_ORIGIN))
        req.add_header("Referer", _clean_header_value(os.environ.get("COMMANDCODE_REFERER") or f"{DEFAULT_MERLIN_ORIGIN}/"))
        req.add_header("X-Merlin-Version", _clean_header_value(os.environ.get("COMMANDCODE_MERLIN_VERSION") or DEFAULT_MERLIN_VERSION))
        req.add_header("X-Request-Timestamp", _clean_header_value(os.environ.get("COMMANDCODE_REQUEST_TIMESTAMP") or _merlin_request_timestamp()))
    else:
        req.add_header("Origin", _clean_header_value(os.environ.get("COMMANDCODE_ORIGIN") or "https://api.commandcode.ai"))
        req.add_header("Referer", _clean_header_value(os.environ.get("COMMANDCODE_REFERER") or "https://api.commandcode.ai/"))
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        with _open_no_redirect(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        log.exception("Command Code request failed")
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
        return {"error": f"Request failed: {e}"}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        if manual:
            return parse_commandcode_credits(manual, args, cfg)
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
        pct_left = tier.get("pct_left")
        pct_used = tier.get("pct_used")
        unit = tier.get("unit") or data.get("unit_label") or "credits"
        label = str(tier.get("label") or unit)[:16]
        b = bar(pct_used or 0.0, no_color=getattr(args, 'no_color', False))
        remaining = tier.get("remaining", 0.0)
        total = tier.get("total")
        if pct_left is None:
            total_text = "?" if total is None else f"{total:.4f}"
            print(f"    {label:<16} {b}    ?% left  {remaining:.4f}/{total_text} {unit}")
        else:
            print(f"    {label:<16} {b}  {pct_left:5.1f}% left  {remaining:.4f}/{total:.4f} {unit}")

    if data.get("plan"):
        print_c(f"    plan             {data['plan']}", "\033[90m", getattr(args, 'no_color', False))

    credits = data.get("credits") or {}
    for key, label in (
        ("purchased", "purchased"),
        ("monthly", "monthly"),
        ("premium_monthly", "premium monthly"),
        ("opensource_monthly", "opensource monthly"),
    ):
        value = credits.get(key, 0.0)
        if value:
            print_c(f"    {label:<16} {value:.4f} credits", "\033[90m", getattr(args, 'no_color', False))
    if credits.get("below_threshold"):
        print_c(f"    threshold        below {credits.get('threshold', 0.0):.4f}", "\033[33m", getattr(args, 'no_color', False))

    daily_usage = data.get("daily_usage") or {}
    monthly_usage = data.get("monthly_usage") or {}
    if daily_usage.get("cost"):
        print_c(f"    daily cost       ${_number(daily_usage.get('cost')):.6f}", "\033[90m", getattr(args, 'no_color', False))
    if monthly_usage.get("cost"):
        print_c(f"    monthly cost     ${_number(monthly_usage.get('cost')):.6f}", "\033[90m", getattr(args, 'no_color', False))
