"""Cline CLI / ClinePass provider — local readiness plus ClinePass quota windows."""

import json
import os
import shutil
import subprocess  # nosec B404
import urllib.error
import urllib.request
from pathlib import Path

from limitlens.core import bar, fmt_reset, print_c, section

CLINE_SETTINGS_PATH = os.path.expanduser("~/.cline/data/settings/providers.json")
CLINE_API_BASE = "https://api.cline.bot"
CLINE_REFRESH_PATH = "/api/v1/auth/refresh"
CLINE_USAGE_PATH = "/api/v1/users/me/plan/usage-limits"

WINDOW_LABELS = {
    "five_hour": "5h",
    "weekly": "weekly",
    "monthly": "monthly",
}


def _is_disabled(config):
    cfg = (config or {}).get("cline", {}) if isinstance(config, dict) else {}
    return str(cfg.get("enabled", True)).lower() in ("false", "0", "no", "off")


def _cline_version():
    try:
        proc = subprocess.run(
            ["cline", "version"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )  # nosec B603
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or proc.stderr).strip()
    return out.splitlines()[0] if out else None


def _load_stored_credentials():
    try:
        raw = Path(CLINE_SETTINGS_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    providers = doc.get("providers") if isinstance(doc, dict) else None
    if not isinstance(providers, dict):
        return None
    for name in ("cline-pass", "cline"):
        prov = providers.get(name)
        if not isinstance(prov, dict):
            continue
        auth = (prov.get("settings") or {}).get("auth") or {}
        access = auth.get("accessToken")
        refresh = auth.get("refreshToken")
        if access or refresh:
            return {
                "access": access or "",
                "refresh": refresh or "",
                "expires_at": auth.get("expiresAt"),
                "account_id": auth.get("accountId"),
                "provider": name,
            }
    return None


def _decode_jwt_exp(token):
    if not token:
        return None
    payload = token.split(".")
    if len(payload) < 2:
        return None
    try:
        import base64

        seg = payload[1]
        seg += "=" * (-len(seg) % 4)
        data = json.loads(base64.urlsafe_b64decode(seg).decode("utf-8"))
        exp = data.get("exp")
        return float(exp) if exp else None
    except (ValueError, TypeError, OSError):
        return None


def _token_expired(creds):
    expires = creds.get("expires_at")
    if expires:
        try:
            val = float(expires)
            if val > 1e12:
                val = val / 1000.0
            return val <= _now_ts() + 30
        except (TypeError, ValueError):
            pass
    exp = _decode_jwt_exp(creds.get("access", "").removeprefix("workos:"))
    if exp:
        return exp <= _now_ts() + 30
    return True


def _now_ts():
    import time

    return time.time()


def _refresh_token(creds):
    refresh = creds.get("refresh")
    if not refresh:
        return None
    body = json.dumps({"refreshToken": refresh, "grantType": "refresh_token"}).encode("utf-8")
    req = urllib.request.Request(
        f"{CLINE_API_BASE}{CLINE_REFRESH_PATH}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            doc = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict) or not data.get("accessToken"):
        return None
    new_access = data["accessToken"]
    if not new_access.startswith("workos:"):
        new_access = f"workos:{new_access}"
        
    new_refresh = data.get("refreshToken") or refresh
    new_expires_at = data.get("expiresAt")
    
    _save_new_token(creds.get("provider"), new_access, new_refresh, new_expires_at)
    
    return {
        "access": new_access,
        "refresh": new_refresh,
        "expires_at": new_expires_at,
        "account_id": (data.get("userInfo") or {}).get("clineUserId") or creds.get("account_id"),
        "provider": creds.get("provider"),
    }


def _save_new_token(provider_name, access, refresh, expires_at):
    if not provider_name:
        return
    try:
        path = Path(CLINE_SETTINGS_PATH)
        if not path.exists():
            return
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("providers"), dict):
            return
        prov = doc["providers"].get(provider_name)
        if not isinstance(prov, dict):
            return
        if "settings" not in prov:
            prov["settings"] = {}
        if "auth" not in prov["settings"]:
            prov["settings"]["auth"] = {}
        auth = prov["settings"]["auth"]

        # Cline stores access token without the workos: prefix
        clean_access = access[7:] if access.startswith("workos:") else access
        auth["accessToken"] = clean_access
        auth["refreshToken"] = refresh
        if expires_at:
            auth["expiresAt"] = expires_at

        _atomic_write_settings(path, doc)
    except (OSError, ValueError, TypeError):
        pass


def _atomic_write_settings(path, doc):
    """Atomically write JSON to path using a temp file in the same directory."""
    import tempfile
    import os
    data = json.dumps(doc, indent=2)
    dir_path = str(path.parent)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix="cline_", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, str(path))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _resolve_access_token(creds):
    if not creds:
        return None
    if not _token_expired(creds):
        token = creds.get("access") or ""
        return token if token.startswith("workos:") else f"workos:{token}"
    refreshed = _refresh_token(creds)
    if refreshed:
        token = refreshed.get("access") or ""
        return token if token.startswith("workos:") else f"workos:{token}"
    return None


def fetch_usage_limits(access_token):
    if not access_token:
        return None
    req = urllib.request.Request(
        f"{CLINE_API_BASE}{CLINE_USAGE_PATH}",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "*/*",
            "Origin": "https://app.cline.bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            doc = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    return doc.get("data")


def get_cline_data(args, config=None):
    if getattr(args, "tool", None) != "cline" and _is_disabled(config):
        return None

    installed = bool(shutil.which("cline"))
    version = _cline_version() if installed else None

    creds = _load_stored_credentials()
    token = _resolve_access_token(creds)
    limits_doc = fetch_usage_limits(token) if token else None
    limits = (limits_doc or {}).get("limits") if isinstance(limits_doc, dict) else None

    if not limits:
        if not installed and not token:
            return {
                "name": "Cline CLI",
                "command": "cline",
                "installed": False,
                "status": "not installed",
                "note": "install Cline CLI to use it from LimitLens",
            }
        if not token:
            return {
                "name": "Cline CLI",
                "command": "cline",
                "installed": installed,
                "version": version,
                "status": "installed" if installed else "not installed",
                "note": "sign in with `cline auth` to fetch ClinePass quota",
            }
        return {
            "name": "Cline CLI",
            "command": "cline",
            "installed": installed,
            "version": version,
            "status": "installed" if installed else "not installed",
            "note": "failed to fetch ClinePass quota (token may need re-auth)",
        }

    windows = []
    for lim in limits:
        if not isinstance(lim, dict):
            continue
        kind = lim.get("type")
        if kind not in WINDOW_LABELS:
            continue
        pct_used = lim.get("percentUsed")
        try:
            pct_used = float(pct_used) if pct_used is not None else None
        except (TypeError, ValueError):
            pct_used = None
        windows.append({
            "type": kind,
            "label": WINDOW_LABELS[kind],
            "pct_used": pct_used,
            "pct_left": (100.0 - pct_used) if pct_used is not None else None,
            "resets_at": lim.get("resetsAt"),
        })

    return {
        "name": "Cline CLI",
        "command": "cline",
        "installed": installed,
        "version": version,
        "status": "installed" if installed else "not installed",
        "windows": windows,
    }


def display_cline_text(data, args):
    if data is None:
        return

    windows = data.get("windows")
    if not windows:
        if data.get("status") != "installed" and not (
            getattr(args, "tool", None) == "cline"
            or getattr(args, "verbose", False)
            or getattr(args, "all", False)
        ):
            return
        section(data.get("name") or "Cline CLI", args)
        status = data.get("status") or "unknown"
        version = data.get("version")
        version_text = f" ({version})" if version else ""
        print_c(f"    status: {status}{version_text}", "", getattr(args, "no_color", False))
        if data.get("note"):
            print_c(f"    note: {data['note']}", "\033[90m", getattr(args, "no_color", False))
        return

    section(data.get("name") or "Cline CLI", args)
    version = data.get("version")
    header = "ClinePass" + (f"  (cline {version})" if version else "")
    print_c(f"    {header}", "\033[90m", getattr(args, "no_color", False))
    no_color = getattr(args, "no_color", False)
    for win in windows:
        label = win.get("label", win.get("type", ""))
        pct_used = win.get("pct_used")
        pct_left = win.get("pct_left")
        rst = fmt_reset(win.get("resets_at"))
        if pct_used is not None and pct_left is not None:
            b = bar(pct_used, width=6, no_color=no_color)
            print_c(f"    quota {label:<6} [{b}] {pct_left:4.0f}%  {rst}", "", no_color)
        else:
            print_c(f"    quota {label:<6} {rst}", "\033[90m", no_color)
