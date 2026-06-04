#!/usr/bin/env python3
"""
LimitLens iTerm2 Status Bar Widget.

This module provides an iTerm2 background script that creates a custom status bar
component. It continuously polls LimitLens for quota statuses and displays the
most relevant available tool (or highest quota) directly in the iTerm2 terminal window.
"""
import asyncio
import iterm2
import json
import os
import subprocess

async def main(connection):
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
    # ==============================================================================
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
            return subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
        cmd = [PYTHON_BIN, "-m", "limitlens", "--json"]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=LIMITLENS_DIR, timeout=15)

    # Icons for each real LimitLens-tracked tool (consistent across menubar + iTerm)
    TOOL_ICONS = {
        "antigravity":  "🪐",   # Antigravity
        "codex":        "⚡",   # OpenAI Codex
        "amp":          "🔥",   # Amp
        "pioneer":      "🧭",   # Pioneer
        "agentrouter":  "🔶",   # Kilo Code / AgentRouter
        "commandcode":  "🖥️",  # Command Code
        "copilot":      "✈️",  # GitHub Copilot
        "cursor":       "🖱️",  # Cursor IDE
        "custom":       "🔧",   # Custom configured tools
    }

    def _tool_icon(tool_key, name):
        """Return a unique icon for the tool, falling back to a generic one."""
        key = (tool_key or "").lower()
        name_lower = (name or "").lower()
        for k, icon in TOOL_ICONS.items():
            if k in key or k in name_lower:
                return icon
        return "🔷"

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

        display_items = []
        for item in recs.get("hard", []):
            tool = item.get("tool", "")
            pct = item.get("headroom_pct", 0)

            if tool == "antigravity" and pct < 20:
                continue
            if tool != "antigravity" and pct < 10:
                continue

            full_name = item.get("name", "Unknown")
            if " (" in full_name:
                full_name = full_name.split(" (")[0]

            icon = _tool_icon(tool, full_name)
            # Compact: icon + bar + percent, e.g.  🪐███99  ⚡██░67  🧠░░░12
            display_items.append(f"{icon}{_bar(pct)}{pct:.0f}")

        if display_items:
            visible = display_items[:4]
            extra = len(display_items) - len(visible)
            suffix = f" +{extra}" if extra > 0 else ""
            return "  ".join(visible) + suffix
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

iterm2.run_forever(main, retry=True)
