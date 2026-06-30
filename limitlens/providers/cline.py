"""Cline CLI provider — status-only local CLI readiness."""

import shutil
import subprocess  # nosec B404

from limitlens.core import print_c, section


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
    return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else None


def get_cline_data(args, config=None):
    if getattr(args, "tool", None) != "cline" and _is_disabled(config):
        return None

    if not shutil.which("cline"):
        return {
            "name": "Cline CLI",
            "command": "cline",
            "installed": False,
            "status": "not installed",
            "note": "install Cline CLI to use it from LimitLens",
        }

    return {
        "name": "Cline CLI",
        "command": "cline",
        "installed": True,
        "version": _cline_version(),
        "status": "installed",
        "note": "quota not exposed by Cline CLI",
    }


def display_cline_text(data, args):
    if data is None:
        return

    show_status = getattr(args, "tool", None) == "cline" or getattr(args, "verbose", False) or getattr(args, "all", False)
    if not show_status:
        return

    section(data.get("name") or "Cline CLI", args)
    status = data.get("status") or "unknown"
    version = data.get("version")
    version_text = f" ({version})" if version else ""
    print_c(f"    status: {status}{version_text}", "", getattr(args, "no_color", False))
    if data.get("note"):
        print_c(f"    note: {data['note']}", "\033[90m", getattr(args, "no_color", False))
