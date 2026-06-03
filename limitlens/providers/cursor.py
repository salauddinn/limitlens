import json
import os
import platform
import sqlite3
import urllib.request

from limitlens.core import bar, print_c, section


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_cursor_token(sys_name):
    paths = []
    if sys_name == "Darwin":
        paths.append(os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"))
    elif sys_name == "Linux":
        paths.append(os.path.expanduser("~/.config/Cursor/User/globalStorage/state.vscdb"))
    elif sys_name == "Windows":
        paths.append(os.path.expandvars(r"%APPDATA%\Cursor\User\globalStorage\state.vscdb"))

    for path in paths:
        if os.path.exists(path):
            try:
                import pathlib
                uri = f"{pathlib.Path(path).as_uri()}?mode=ro"
                conn = sqlite3.connect(uri, uri=True)
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'")
                row = cursor.fetchone()
                conn.close()
                if row:
                    return row[0]
            except Exception:
                pass
    return None


def fetch_cursor_usage(token):
    req = urllib.request.Request("https://api2.cursor.sh/auth/usage")
    req.add_header("Authorization", f"Bearer {token}")
    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def http_error_302(self, req, fp, code, msg, headers):
            raise Exception("Redirects are not allowed.")
        http_error_301 = http_error_303 = http_error_307 = http_error_302
    try:
        opener = urllib.request.build_opener(NoRedirectHandler())
        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def parse_cursor_data(payload, args, cfg=None):
    if not payload:
        return {"error": "Failed to fetch Cursor usage (token invalid or network error)"}
        
    cfg = cfg or {}
    
    gpt4 = payload.get("gpt-4") or {}
    # Sometimes it's nested in a different way or they have 'premium-models'
    premium = payload.get("premium-models") or gpt4
    
    num_requests = _number(premium.get("numRequests", gpt4.get("numRequests")), 0.0)
    max_requests = _number(premium.get("maxRequestUsage", gpt4.get("maxRequestUsage")), 0.0)
    
    if max_requests > 0:
        remaining = max(0.0, max_requests - num_requests)
        pct_left = (remaining / max_requests) * 100.0
        pct_used = (num_requests / max_requests) * 100.0
    else:
        remaining = num_requests
        max_requests = None
        pct_left = None
        pct_used = None

    tiers = [{
        "label": "Premium Fast Requests",
        "remaining": remaining,
        "total": max_requests,
        "used": num_requests,
        "pct_left": pct_left,
        "pct_used": pct_used,
        "unit": "req"
    }]
    
    return {
        "name": cfg.get("name") or "Cursor",
        "startOfMonth": payload.get("startOfMonth"),
        "tiers": tiers
    }


def get_cursor_data(args, config=None):
    cfg = (config or {}).get("cursor", {})
    if str(cfg.get("enabled", "true")).lower() == "false":
        return None
        
    token = get_cursor_token(platform.system())
    if not token:
        return {"error": "Cursor access token not found in local state database."}
        
    payload = fetch_cursor_usage(token)
    return parse_cursor_data(payload, args, cfg)


def display_cursor_text(data, args):
    if data is None:
        return
        
    if "error" in data:
        if args.verbose:
            section("Cursor", args)
            print_c(f"    Error: {data['error']}", "\033[91m", getattr(args, 'no_color', False))
        return
        
    section(data.get("name", "Cursor"), args)
    
    tiers = data.get("tiers", [])
    if not tiers:
        print_c("    No usage data found.", "\033[90m", getattr(args, 'no_color', False))
        
    for tier in tiers:
        label = tier.get("label", "Requests")
        pct_left = tier.get("pct_left")
        pct_used = tier.get("pct_used")
        used = tier.get("used", 0)
        total = tier.get("total")
        unit = tier.get("unit", "req")
        
        if pct_left is not None and total is not None:
            b = bar(pct_used, no_color=getattr(args, 'no_color', False))
            print_c(f"    {label}: {pct_left:5.1f}% left  {b}  ({int(used)}/{int(total)} {unit})", "", getattr(args, 'no_color', False))
        else:
            print_c(f"    {label}: {int(used)} {unit} used (Unlimited/Enterprise)", "\033[94m", getattr(args, 'no_color', False))
