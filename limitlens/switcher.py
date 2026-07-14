"""
AI Tool Context Switcher — interactive tool to switch context and run selected CLI tools.

Reads quota and recommendations via LimitLens and executes the chosen tool in place.
"""

import os
import sys
import argparse
import shlex
from concurrent.futures import ThreadPoolExecutor

from .core import load_limitlens_config, parse_to_utc, fmt_reset, print_c
from .recommendations import compute_recommendations
from .providers import PROVIDER_DESCRIPTORS

from .logging import get_logger
log = get_logger("limitlens.switcher")

class SwitchArgs:
    def __init__(self, tool="all", json=False, redact=True, sync_codex=False, verbose=False, no_color=False):
        self.tool = tool
        self.json = json
        self.redact = redact
        self.sync_codex = sync_codex
        self.verbose = verbose
        self.no_color = no_color

def collect_results(config, args):
    import sys
    result = {}
    active_descs = []
    for desc in PROVIDER_DESCRIPTORS.values():
        if desc.key == "copilot_cli":
            continue
        if args.tool == desc.key or any(args.tool == a for a in desc.aliases):
            active_descs.append(desc)
        elif args.tool == "all" and str(config.get(desc.config_key, {}).get("enabled", desc.default_enabled)).lower() not in ("false", "0", "no"):
            active_descs.append(desc)

    def _call_fetch(desc, args, config):
        fn_name = f"get_{desc.key}_data"
        cli_fn = getattr(sys.modules[__name__], fn_name, None)
        if cli_fn and hasattr(cli_fn, "mock_calls"):
            try: return cli_fn(args, config)
            except TypeError:
                try: return cli_fn(args)
                except TypeError: return cli_fn(config)
        return desc.fetch(args, config)

    max_workers = max(16, len(active_descs) or 1)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fetchers = {
            desc.key: executor.submit(_call_fetch, desc, args, config)
            for desc in active_descs
        }

        for key, fut in fetchers.items():
            try:
                result[key] = fut.result()
            except Exception as e:
                log.exception(f"{key} provider failed")
                message = f"{key} provider failed: {type(e).__name__}: {e}"
                if key == "opencode":
                    result[key] = {
                        "opencode": {"error": message},
                        "copilot_cli": {"disabled": True},
                    }
                else:
                    result[key] = {"error": message}
    return result

def main():
    parser = argparse.ArgumentParser(description="Interactive tool switcher based on LimitLens quotas")
    parser.add_argument("-t", "--tool", help="Directly switch context to a tool by name/command (e.g. amp, codex, agy)")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    args, forwarded = parser.parse_known_args()
    no_color = args.no_color
    config = load_limitlens_config()
    switch_args = SwitchArgs(no_color=no_color)

    print_c("  ⟲  fetching quotas and checking recommendations...", "\033[90m", no_color)
    result = collect_results(config, switch_args)
    recs = compute_recommendations(result, parse_to_utc, fmt_reset)
    cands = recs["all_candidates"]

    # Filter out candidates that do not have a runnable CLI command
    runnable_candidates = [
        c for c in cands
        if c.get("command") and not c["command"].startswith("use ")
    ]

    if not runnable_candidates:
        print_c("  ⚠ no runnable CLI tools discovered.", "\033[31m", no_color)
        sys.exit(1)

    target_cand = None
    if args.tool:
        tool_query = args.tool.lower()
        for c in runnable_candidates:
            if tool_query in (c["tool"].lower(), c["name"].lower(), c["command"].lower()):
                target_cand = c
                break
        if not target_cand:
            print_c(f"  ⚠ requested tool '{args.tool}' not found or not runnable.", "\033[31m", no_color)
            print_c("  Available runnable tools:", "\033[90m", no_color)
            for c in runnable_candidates:
                print(f"    - {c['tool']}  (command: {c['command']})")
            sys.exit(1)

    if not target_cand:
        print("\033[2J\033[H", end="") # Clear screen
        print_c("  ═══ AI Tool Context Switcher ═══", "\033[1;36m", no_color)
        print_c("  Select a tool to switch context and execute it in place.\n", "\033[90m", no_color)

        print_c("  💡 Recommendations from LimitLens:", "\033[1;32m", no_color)
        for tier in ("hard", "quick", "cli"):
            picks = recs[tier]
            if picks:
                runnable_picks = [p for p in picks if p.get("command") and not p["command"].startswith("use ")]
                if runnable_picks:
                    top_pick = runnable_picks[0]
                    tier_label = {"hard": "Hard Tasks", "quick": "Quick Edits", "cli": "CLI/Pairing"}[tier]
                    print(f"    • {tier_label:12}: {top_pick['name']} ({top_pick['headroom_pct']:.0f}% quota left)")
        print()

        print_c("  Available tools:", "\033[1m", no_color)
        for idx, c in enumerate(runnable_candidates, 1):
            pct = c["headroom_pct"]
            if no_color:
                pct_str = f"{pct:.0f}%"
            elif pct > 50:
                pct_str = f"\033[32m{pct:.0f}%\033[0m"
            elif pct > 15:
                pct_str = f"\033[33m{pct:.0f}%\033[0m"
            else:
                pct_str = f"\033[31m{pct:.0f}%\033[0m"

            note_str = f" · {c['note']}" if c.get('note') else ""
            print(f"  [{idx}] {c['name']}")
            print_c(f"      Quota:   {pct_str} left{note_str}", "\033[90m", no_color)
            print_c(f"      Command: {c['command']}", "\033[90m", no_color)
            print()

        default_idx = 1
        cli_picks = [p for p in recs["cli"] if p.get("command") and not p["command"].startswith("use ")]
        if cli_picks:
            for idx, c in enumerate(runnable_candidates, 1):
                if c["command"] == cli_picks[0]["command"]:
                    default_idx = idx
                    break

        prompt_msg = f"  Select a tool [1-{len(runnable_candidates)}] (default: {default_idx}): "
        try:
            user_input = input(prompt_msg).strip()
            if not user_input:
                choice_idx = default_idx
            else:
                choice_idx = int(user_input)
                if choice_idx < 1 or choice_idx > len(runnable_candidates):
                    raise ValueError
        except (KeyboardInterrupt, EOFError):
            print("\n  Aborted.")
            sys.exit(0)
        except ValueError:
            print_c("  ⚠ invalid selection.", "\033[31m", no_color)
            sys.exit(1)

        target_cand = runnable_candidates[choice_idx - 1]

    cmd_str = target_cand["command"]
    cmd_parts = shlex.split(cmd_str) if cmd_str else []
    if cmd_parts:
        from .runner import _executable_exists
        if not _executable_exists(cmd_parts):
            executable = next((p for p in cmd_parts if "=" not in p and p not in ("env", "/usr/bin/env")), cmd_parts[0])
            print_c(f"  ⚠ Tool '{executable}' is not installed or not in PATH.", "\033[31m", no_color)
            sys.exit(1)

    if forwarded:
        args_str = " ".join(shlex.quote(a) for a in forwarded)
        full_command = f"{cmd_str} {args_str}"
    else:
        full_command = cmd_str

    print_c(f"\n  ⚡ Switching context: executing `{full_command}` in place...\n", "\033[1;32m", no_color)

    shell = os.environ.get("SHELL", "cmd.exe" if sys.platform == "win32" else "sh")
    try:
        if sys.platform == "win32":
            # Bug 11: os.execvp is not available on Windows; use subprocess instead.
            import subprocess  # nosec B404
            proc = subprocess.run(  # nosec B602
                full_command,
                shell=True,
            )
            sys.exit(proc.returncode)
        else:
            os.execvp(shell, [shell, "-c", full_command])  # nosec B606
    except Exception as e:
        log.exception("Failed to execute command")
        print_c(f"  \u26a0 Failed to execute command: {e}", "\033[31m", no_color)
        sys.exit(1)
