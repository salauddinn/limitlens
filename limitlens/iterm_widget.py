#!/usr/bin/env python3
"""
LimitLens iTerm2 Status Bar Widget.

This module ships an iTerm2 background script that creates a custom status bar
component. It continuously polls LimitLens for quota statuses and displays the
most relevant available tool (or highest quota) directly in the iTerm2 terminal
window.

When LimitLens is installed (e.g. via ``pipx install limitlens``), run
``limitlens-iterm-widget --install`` to drop a version-matched copy of this
widget into iTerm2's Scripts directory, or ``limitlens-iterm-widget --print`` to
emit the source so it can be piped into a shell config.

Note: ``iterm2`` is imported lazily so that this module can be imported by the
console script in environments (such as a pipx venv) where the iTerm2 Python API
is not installed.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys

WIDGET_FILENAME = "limitlens_widget.py"


def _widget_source():
    """Return the source of this file so it can be installed into iTerm2."""
    path = os.path.realpath(__file__)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _iterm_scripts_dir():
    """Return the default iTerm2 Scripts directory on macOS."""
    return os.path.join(
        os.path.expanduser("~"),
        "Library",
        "Application Support",
        "iTerm2",
        "Scripts",
    )


def install_widget(target_dir=None):
    """Install a copy of this widget into iTerm2's Scripts directory."""
    target_dir = target_dir or _iterm_scripts_dir()
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, WIDGET_FILENAME)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(_widget_source())
    os.chmod(target, 0o755)
    return target


def main():
    """CLI entry point: install or print the LimitLens iTerm2 status bar widget."""
    parser = argparse.ArgumentParser(
        prog="limitlens-iterm-widget",
        description="Install the LimitLens iTerm2 status bar widget.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the widget into iTerm2's Scripts directory (default action).",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the widget source to stdout so it can be piped to a file.",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Custom target directory for --install (default: iTerm2 Scripts dir).",
    )
    args = parser.parse_args()

    if args.print:
        sys.stdout.write(_widget_source())
        return

    target = install_widget(args.dir)
    print(f"LimitLens iTerm2 widget installed to: {target}")


async def iterm_main(connection):
    """iTerm2 coroutine entry point invoked by ``iterm2.run_forever``."""
    import iterm2

    print("Starting LimitLens registration v4...")

    component = iterm2.StatusBarComponent(
        short_description="LimitLens Widget",
        detailed_description="Shows the best AI tool to avoid quota waste",
        knobs=[],
        exemplar="🪐███99  ⚡██░67  🔥█░░30",
        update_cadence=900.0,
        identifier="com.limitlens.status"
    )

    # ==============================================================================
    # CONFIGURATION
    # If you copy this script to your iTerm2 Scripts folder, you MUST set this
    # to the absolute path of your limitlens clone!
    # E.g., USER_LIMITLENS_DIR = "/Users/name/Projects/limitlens"
    # ===============================================================================
    USER_LIMITLENS_DIR = ""

    # Auto-detect if running directly from the repository (or via symlink)
    LIMITLENS_DIR = os.path.dirname(os.path.realpath(__file__))

    # Fallback for when the script is copied into iTerm2's application support directory
    if "Scripts" in LIMITLENS_DIR or "iterm2" in LIMITLENS_DIR.lower():
        LIMITLENS_DIR = os.path.expanduser(USER_LIMITLENS_DIR)

    def _find_venv_python(venv_dir):
        """Find any python3.x binary in the venv, regardless of patch version."""
        bin_dir = os.path.join(venv_dir, "bin")
        # Try exact 'python3' symlink first (most venvs create this)
        candidate = os.path.join(bin_dir, "python3")
        if os.path.exists(candidate):
            return candidate

        if not os.path.isdir(bin_dir):
            return None

        # Fall back to any versioned python3.x binary, avoiding glob path wildcard issues
        matches = []
        try:
            for name in os.listdir(bin_dir):
                if name.startswith("python3.") and name[-1].isdigit():
                    matches.append(name)
        except OSError:
            return None

        if not matches:
            return None

        def _parse_version(name):
            ver_str = name[len("python3."):]
            try:
                return [int(x) for x in ver_str.split(".") if x.isdigit()]
            except Exception:
                return []

        matches.sort(key=_parse_version)
        return os.path.join(bin_dir, matches[-1])

    # Detect python path — prefer the venv python, then homebrew, then system
    venv_python = _find_venv_python(os.path.join(LIMITLENS_DIR, ".venv"))
    if venv_python:
        PYTHON_BIN = venv_python
    elif os.path.exists("/opt/homebrew/bin/python3"):
        PYTHON_BIN = "/opt/homebrew/bin/python3"
    elif os.path.exists("/usr/local/bin/python3"):
        PYTHON_BIN = "/usr/local/bin/python3"
    else:
        PYTHON_BIN = "python3"

    state = {"status": "⏳ Loading..."}

    def fetch_status_sync():
        import shutil
        limitlens_bin = shutil.which("limitlens")

        # Fallback for pipx installs when iTerm GUI doesn't inherit shell PATH
        if not limitlens_bin and os.path.exists(os.path.expanduser("~/.local/bin/limitlens")):
            limitlens_bin = os.path.expanduser("~/.local/bin/limitlens")

        if limitlens_bin:
            cmd = [limitlens_bin, "--json"]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)

        cmd = [PYTHON_BIN, "-m", "limitlens", "--json"]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=LIMITLENS_DIR, timeout=15, stdin=subprocess.DEVNULL)

    def _tool_icon(tool_key, name):
        """Return a unique icon: known tool → fixed icon, custom → keyword match
        or deterministic pool pick based on name adler32 hash."""
        import sys
        if LIMITLENS_DIR and LIMITLENS_DIR not in sys.path:
            sys.path.insert(0, LIMITLENS_DIR)
        try:
            from limitlens.core import get_tool_icon
            return get_tool_icon(tool_key=tool_key, name=name)
        except ImportError:
            # Fallback when installed via pipx and package is unresolvable
            known = {
                "antigravity": "🪐", "codex": "⚡", "amp": "🔥", "pioneer": "🧭",
                "kilo": "🔶", "commandcode": "🖥️", "claude": "🤖", "copilot": "✈️"
            }
            return known.get(tool_key, "▪️")

    def _bar(pct):
        """Mini 3-char fill bar: ███ / ██░ / █░░ / ░░░"""
        if pct is None:
            return "░░░"
        filled = round(pct / 100 * 3)
        return "█" * filled + "░" * (3 - filled)

    def status_from_proc(proc):
        if proc is None:
            return "⚠️ Set USER_LIMITLENS_DIR"
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().split("\n")[-1]
            return f"⚠️ {err[:15]}" if err else "⚠️ LimitLens Err"

        data = json.loads(proc.stdout)
        recs = data.get("recommendations", {})

        def format_item(item):
            tool = item.get("tool", "")
            pct = item.get("headroom_pct", 0)
            full_name = item.get("name", "Unknown")
            if " (" in full_name:
                full_name = full_name.split(" (")[0]
            icon = _tool_icon(tool, full_name)
            return f"{icon}{_bar(pct)}{pct:.0f}"

        hard_recs = recs.get("hard", [])
        waste_recs = recs.get("waste_watch", [])
        all_cands = recs.get("all_candidates", [])

        seen_names = set()
        selected = []

        def add_item(item):
            name = item.get("name")
            if name and name not in seen_names:
                seen_names.add(name)
                selected.append(item)
                return True
            return False

        # 1. Action slot (Top recommendation)
        if hard_recs:
            add_item(hard_recs[0])

        # 2. Expiring slot (Urgent waste)
        for w in waste_recs:
            if add_item(w):
                break

        # 3. Danger slot (Lowest headroom under 20%)
        if all_cands:
            for c in sorted(all_cands, key=lambda c: c.get("headroom_pct", 100)):
                if c.get("headroom_pct", 100) < 20:
                    if add_item(c):
                        break

        # 4. Fill remaining up to 3 slots with next best tasks
        for h in hard_recs:
            if len(selected) >= 3:
                break
            add_item(h)

        display_items = [format_item(item) for item in selected]

        if display_items:
            return "  ".join(display_items)
        return "⚪ No quota"

    async def refresh_state_once(loop):
        try:
            proc = await loop.run_in_executor(None, fetch_status_sync)
            state["status"] = status_from_proc(proc)
        except subprocess.TimeoutExpired:
            state["status"] = "🤖 LimitLens: Timeout"
        except Exception as e:
            state["status"] = f"🤖 Err: {e}"

    async def poll_status():
        loop = asyncio.get_running_loop()
        while True:
            await refresh_state_once(loop)
            await asyncio.sleep(900)

    @iterm2.StatusBarRPC
    async def coro(knobs, session_id=iterm2.Reference("id")):
        # Return instantly to prevent iTerm2 from timing out
        return state["status"]

    # Retry registration forever with backoff. iTerm2 can leave a stale
    # DUPLICATE_SERVER_ORIGINATED_RPC registration around after a crash/restart;
    # returning here makes macOS show "Script Failed", so keep the script alive
    # until iTerm2 accepts the registration.
    attempt = 0
    while True:
        try:
            await component.async_register(connection, coro)
            print("Registration successful for com.limitlens.status")
            break
        except Exception as e:
            attempt += 1
            delay = min(2 ** min(attempt, 8), 300)
            print(f"Registration attempt {attempt} failed: {e} — retrying in {delay}s")
            await asyncio.sleep(delay)

    # Start the background polling task only after successful registration
    asyncio.create_task(poll_status())


if __name__ == "__main__":
    import iterm2

    iterm2.run_forever(iterm_main, retry=True)
