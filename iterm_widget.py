#!/usr/bin/env python3
import asyncio
import glob
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
        exemplar="💡 AI: 80%",
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
        # Fall back to any versioned python3.x binary
        matches = sorted(glob.glob(os.path.join(bin_dir, "python3.*")))
        if matches:
            return matches[-1]  # pick the highest version
        return None

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

    state = {"status": "💡 AI: Loading..."}

    def fetch_status_sync():
        script_path = os.path.join(LIMITLENS_DIR, "limitlens.py")
        if not os.path.exists(script_path):
            return None
        cmd = [PYTHON_BIN, script_path, "--json"]
        return subprocess.run(cmd, capture_output=True, text=True)

    async def poll_status():
        loop = asyncio.get_running_loop()
        while True:
            try:
                proc = await loop.run_in_executor(None, fetch_status_sync)
                
                if proc is None:
                    state["status"] = "🤖 Err: Set USER_LIMITLENS_DIR in widget script"
                elif proc.returncode == 0:
                    data = json.loads(proc.stdout)
                    
                    recs = data.get("recommendations", {})
                    candidates = recs.get("hard", [])
                    
                    display_items = []
                    for item in candidates:
                        tool = item.get("tool", "")
                        pct = item.get("headroom_pct", 0)
                        
                        if tool == "antigravity" and pct < 20:
                            continue
                        if tool != "antigravity" and pct < 10:
                            continue
                            
                        full_name = item.get("name", "Unknown")
                        # Strip bottleneck info like " (weekly)"
                        if " (" in full_name:
                            full_name = full_name.split(" (")[0]
                            
                        if " → " in full_name:
                            prof, model = full_name.split(" → ", 1)
                            prof = prof.split(":", 1)[-1]
                            display_name = f"{prof}:{model.split()[0]}"
                        else:
                            display_name = full_name.replace("codex-", "")
                            
                        display_items.append(f"{display_name}:{pct:.0f}%")
                    
                    if display_items:
                        state["status"] = "💡 " + " | ".join(display_items)
                    else:
                        state["status"] = "🤖 LimitLens: No quotas available"
                else:
                    state["status"] = "🤖 LimitLens: Err"
            except Exception as e:
                state["status"] = f"🤖 Err: {e}"
            
            await asyncio.sleep(900)

    # Start the background polling task
    asyncio.create_task(poll_status())

    @iterm2.StatusBarRPC
    async def coro(knobs, session_id=iterm2.Reference("id")):
        # Return instantly to prevent iTerm2 from timing out
        return state["status"]

    try:
        await component.async_register(connection, coro)
        print("Registration successful for com.limitlens.status")
    except Exception as e:
        print(f"Registration failed: {e}")

iterm2.run_forever(main, retry=True)
