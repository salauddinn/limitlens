"""Grok (xAI) provider — reads login status from ~/.grok/auth.json.

Reads non-sensitive fields (email, team_id, expires_at, tier) from the local
auth file written by the Grok CLI.  When an SSO cookie is available (via
``GROK_SSO_COOKIE`` env var or the OS keychain), it is transmitted to
grok.com's internal gRPC-Web billing endpoint to fetch quota usage.  The SSO
cookie is never written to the log file.
"""

import json
import logging
import os
import socket
import struct
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from limitlens.core import (
    redact_email,
    print_c,
    section,
    bar,
    NoRedirectHandler,
)

log = logging.getLogger("limitlens.providers.grok")

GROK_AUTH_PATH = os.path.expanduser("~/.grok/auth.json")
GROK_CONFIG_PATH = os.path.expanduser("~/.grok/config.toml")
GROK_DIR = os.path.expanduser("~/.grok")

TIER_LABELS = {
    0: "Free",
    1: "Starter",
    2: "Basic",
    3: "Pro",
    4: "Pro",
    5: "Enterprise",
}


def _safe_exists(p):
    try:
        return os.path.exists(p)
    except OSError:
        return False


def _load_auth():
    """Read ~/.grok/auth.json and return non-sensitive fields only.

    Explicitly skips 'key' and 'refresh_token' fields.
    """
    try:
        raw = Path(GROK_AUTH_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict) or not doc:
        return None

    # auth.json is keyed by "{issuer}::{client_id}" → one entry per account
    accounts = []
    for entry in doc.values():
        if not isinstance(entry, dict):
            continue
        accounts.append({
            "email": entry.get("email"),
            "first_name": entry.get("first_name"),
            "team_id": entry.get("team_id"),
            "expires_at": entry.get("expires_at"),
            "auth_mode": entry.get("auth_mode"),
            "tier": entry.get("tier"),
        })

    return accounts


def _load_default_model():
    """Read default model from ~/.grok/config.toml (best-effort)."""
    try:
        raw = Path(GROK_CONFIG_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("default") and "=" in line:
            _, _, value = line.partition("=")
            return value.strip().strip('"').strip("'") or None
    return None


def _login_status(expires_at):
    """Return ('logged_in'|'expired'|'unknown', human_readable_str)."""
    if not expires_at:
        return "unknown", "unknown"
    try:
        dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt > now:
            return "logged_in", "logged in"
        return "expired", "session expired (run `grok login`)"
    except (ValueError, TypeError):
        return "unknown", "unknown"


def _parse_grpc_percent(data):
    """Extract float from gRPC-Web proto (field 1 of outer field 1).

    Frame layout (gRPC-Web):
        flag(1) | length(4 BE) | body(length bytes)
    Protobuf body:
        tag 0x0a (field 1, LEN) | submsg_len(varint) | tag 0x0d (field 1, F32) | float(4 LE)
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) < 6:
        return None
    try:
        idx = 0
        while idx + 5 <= len(data):
            flag = data[idx]
            length = struct.unpack('>I', data[idx+1:idx+5])[0]
            # [A4] Reject truncated or unreasonably large frames
            if idx + 5 + length > len(data) or length > 1 << 20:
                log.debug("grok parser: bogus frame length %d at idx %d", length, idx)
                return None
            body = data[idx+5:idx+5+length]
            idx += 5 + length
            if flag == 0 and body[:1] == b'\x0a':
                # Decode varint (submessage length) — cap at 10 bytes [A7]
                pos = 1
                terminated = False
                for _ in range(10):  # protobuf varints are at most 10 bytes
                    if pos >= len(body):
                        break
                    b = body[pos]
                    pos += 1
                    if not (b & 0x80):
                        terminated = True
                        break
                if not terminated:
                    return None
                # [A10] bounds check before unpacking the 4-byte float
                if pos + 5 <= len(body) and body[pos] == 0x0d:
                    return struct.unpack('<f', body[pos+1:pos+5])[0]
    except (struct.error, IndexError) as exc:
        log.debug("grok parser: malformed frame: %s", exc)
    return None





def fetch_grok_usage(sso_cookie):
    """Fetch usage from Grok's internal gRPC-Web billing endpoint.

    Returns a float (percentage used 0–100) on success, or a dict with an
    ``"error"`` key describing the failure reason so callers can distinguish
    auth failures from transient network errors.
    """
    url = 'https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig'
    req = urllib.request.Request(url, data=b'\x00\x00\x00\x00\x00', method='POST')
    req.add_header('content-type', 'application/grpc-web+proto')
    req.add_header('x-grpc-web', '1')
    req.add_header('x-user-agent', 'connect-es/2.1.1')
    req.add_header('origin', 'https://grok.com')
    req.add_header('referer', 'https://grok.com/')
    req.add_header('user-agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    # Bug 39: strip CR/LF to prevent CRLF header injection via the sso_cookie value.
    sso_cookie_safe = sso_cookie.replace("\r", "").replace("\n", "")
    req.add_header('cookie', f'sso={sso_cookie_safe}; sso-rw={sso_cookie_safe}')
    try:
        opener = urllib.request.build_opener(NoRedirectHandler())
        with opener.open(req, timeout=10) as resp:
            if resp.status != 200:
                log.warning("grok: HTTP %s from billing endpoint", resp.status)
                return {"error": f"http_{resp.status}"}
            data = resp.read()
            return _parse_grpc_percent(data)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            log.warning("grok: HTTP 403 — SSO cookie may have expired")
            return {"error": "auth_expired", "detail": "SSO cookie may have expired; re-auth with grok CLI"}
        log.warning("grok: HTTP %s from billing endpoint", exc.code)
        return {"error": f"http_{exc.code}"}
    except urllib.error.URLError as exc:
        log.warning("grok: network error: %s", exc.reason)
        return {"error": "network", "detail": str(exc.reason)}
    except (socket.gaierror, TimeoutError) as exc:
        log.warning("grok: DNS/timeout error: %s", exc)
        return {"error": "dns_or_timeout", "detail": str(exc)}


def get_grok_data(args, config=None):
    installed = _safe_exists(GROK_DIR)
    if not installed:
        return {
            "name": "Grok",
            "command": "grok",
            "installed": False,
            "status": "not_installed",
            "note": "Install Grok CLI from https://grok.com",
        }

    auths = _load_auth()
    default_model = _load_default_model()

    if not auths:
        return {
            "name": "Grok",
            "command": "grok",
            "installed": True,
            "status": "not_logged_in",
            "note": "run `grok login` to sign in",
            "default_model": default_model,
        }

    # Try to fetch usage if we have an SSO cookie
    sso_cookie = os.environ.get("GROK_SSO_COOKIE")
    if not sso_cookie:
        try:
            from limitlens.keychain import get_keychain_token
            sso_cookie = get_keychain_token("grok")
        except ImportError:
            pass

    windows = []
    if sso_cookie:
        pct_used = fetch_grok_usage(sso_cookie)
        if pct_used is not None and not isinstance(pct_used, dict):
            windows.append({
                "type": "weekly",
                "label": "weekly",
                "pct_used": pct_used,
                "pct_left": max(0.0, 100.0 - pct_used)
            })

    accounts = []
    for auth in auths:
        login_state, login_label = _login_status(auth.get("expires_at"))

        email = auth.get("email")
        if email and getattr(args, "redact", True):
            email = redact_email(email)

        tier = auth.get("tier")
        tier_label = TIER_LABELS.get(tier) if tier is not None else None
        
        accounts.append({
            "email": email,
            "first_name": auth.get("first_name"),
            "team_id": auth.get("team_id"),
            "tier": tier,
            "tier_label": tier_label,
            "auth_mode": auth.get("auth_mode"),
            "status": login_state,
            "login_label": login_label,
        })

    return {
        "name": "Grok",
        "command": "grok",
        "installed": True,
        "status": "logged_in",
        "default_model": default_model,
        "accounts": accounts,
        "windows": windows,
    }


def display_grok_text(data, args):
    if data is None:
        return

    no_color = getattr(args, "no_color", False)
    verbose = getattr(args, "verbose", False)
    show_all = getattr(args, "all", False)
    is_explicit = getattr(args, "tool", None) == "grok"

    status = data.get("status", "unknown")

    # Only show in default view if logged in; always show with --verbose/--all/--tool grok
    if status not in ("logged_in",) and not (verbose or show_all or is_explicit):
        return

    section("Grok", args)

    email = data.get("email") or ""
    tier_label = data.get("tier_label") or ""
    login_label = data.get("login_label") or status
    default_model = data.get("default_model") or ""

    if status == "not_installed":
        print_c("    status         not installed", "\033[90m", no_color)
        if data.get("note"):
            print_c(f"    note           {data['note']}", "\033[90m", no_color)
        return

    if status == "not_logged_in":
        print_c("    status         not signed in", "\033[33m", no_color)
        if data.get("note"):
            print_c(f"    note           {data['note']}", "\033[90m", no_color)
        return

    if status == "expired":
        print_c(f"    status         {login_label}", "\033[33m", no_color)
        if email:
            print_c(f"    account        {email}", "\033[90m", no_color)
        return

    # Logged in
    color = "\033[32m"
    accounts = data.get("accounts", [])
    
    if not accounts:
        # Fallback if somehow accounts is empty
        print_c(f"    status         {status}", color, no_color)
    elif len(accounts) == 1:
        acc = accounts[0]
        print_c(f"    status         {acc.get('login_label', status)}", "\033[32m" if acc.get("status") == "logged_in" else "\033[33m", no_color)
        if acc.get("email"):
            print_c(f"    account        {acc.get('email')}", "\033[90m", no_color)
        if acc.get("tier_label"):
            print_c(f"    plan           {acc.get('tier_label')}", "\033[90m", no_color)
        if default_model:
            print_c(f"    model          {default_model}", "\033[90m", no_color)
        if verbose and acc.get("auth_mode"):
            print_c(f"    auth           {acc.get('auth_mode')}", "\033[90m", no_color)
    else:
        print_c(f"    accounts       {len(accounts)} configured", "\033[90m", no_color)
        if default_model:
            print_c(f"    model          {default_model}", "\033[90m", no_color)
        for i, acc in enumerate(accounts):
            marker = "├─" if i < len(accounts) - 1 else "└─"
            em = acc.get("email", "unknown")
            st = acc.get("login_label", status)
            pl = acc.get("tier_label", "")
            if pl:
                st = f"{st}, {pl}"
            print_c(f"    {marker} {em} ({st})", "\033[90m", no_color)

    # Show usage bar if we fetched it
    windows = data.get("windows", [])
    if windows:
        for win in windows:
            label = win.get("label", "")
            pct_used = win.get("pct_used")
            pct_left = win.get("pct_left")
            if pct_used is not None and pct_left is not None:
                b = bar(pct_used, width=6, no_color=no_color)
                print_c(f"    quota {label:<6} [{b}] {pct_left:4.0f}%", "", no_color)
    else:
        # Give a hint on how to enable usage tracking
        if verbose:
            print_c("    note           usage hidden (missing SSO cookie)", "\033[90m", no_color)
