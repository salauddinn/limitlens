"""
CLI entrypoint — argparse, concurrent dispatch, and main loop.

This module wires together the provider registry, recommendations engine,
and waste tracker into the unified ``limitlens`` command.
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .core import (
    print_c,
    format_timestamp,
    load_limitlens_config,
    parse_to_utc,
    fmt_reset,
)
from .providers import (
    get_codex_data, display_codex_text,
    get_amp_data, display_amp_text,
    get_antigravity_data, display_antigravity_text,
    get_opencode_data, display_opencode_text,
    get_pi_data, display_pi_text,
    get_pioneer_data, display_pioneer_text,
    get_agentrouter_data, display_agentrouter_text,
    get_commandcode_data, display_commandcode_text,
    get_custom_data, display_custom_text,
)
from .providers.observed import display_at_glance


def main():
    parser = argparse.ArgumentParser(description="Unified status checker for Codex, Amp, and Antigravity")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    parser.add_argument("--redact", action="store_true", default=True, help="Redact PII like emails and account paths (default: True)")
    parser.add_argument("--no-redact", action="store_false", dest="redact", help="Show PII without redaction")
    parser.add_argument("--tool", choices=["codex", "amp", "antigravity", "opencode", "pi", "pioneer", "agentrouter", "commandcode", "custom", "all"], default="all", help="Check specific tool")
    parser.add_argument("--watch", action="store_true", help="Refresh continuously for live status updates")
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds when using --watch (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Show detailed rows and low-level warnings")
    parser.add_argument("--sync-codex", action="store_true", help="Refresh all Codex accounts before showing status")
    parser.add_argument("--all", action="store_true", help="Show all limits, bypassing auto-hide rules")
    parser.add_argument("--no-recommend", action="store_true", help="Skip the recommendation block")
    parser.add_argument("--reco", action="store_true", help="Print only the recommendation block (skip full status)")
    parser.add_argument("--hard", action="store_true", help="One-line recommendation for hard tasks (multi-file, refactor)")
    parser.add_argument("--quick", action="store_true", help="One-line recommendation for quick edits / grunt work")
    parser.add_argument("--cli", action="store_true", help="One-line recommendation for CLI / scripting / pair-prog")
    parser.add_argument("--waste", action="store_true", help="Show waste report (%% of quota wasted at reset over last N days)")
    parser.add_argument("--days", type=int, default=7, help="Window for --waste report (default: 7)")
    parser.add_argument("--record", action="store_true", help="Quietly record a snapshot for waste tracking, then exit")
    parser.add_argument("--no-record", action="store_true", help="Skip snapshot recording on this run")
    parser.add_argument("--reset-waste", action="store_true", help="Delete all recorded waste snapshots, then exit")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")

    tool_label = {
        "codex": "Codex",
        "amp": "Amp",
        "antigravity": "Antigravity",
        "opencode": "observed usage",
        "pi": "Pi usage",
        "pioneer": "Pioneer",
        "agentrouter": "AgentRouter",
        "commandcode": "Command Code",
        "custom": "custom tool",
        "all": "AI tool",
    }[args.tool]
    config = load_limitlens_config()
    codex_refresh_attempts = {}
    codex_refresh_cooldown = 300.0
    sync_codex_pending = bool(args.sync_codex)

    def _provider_error_payload(key, err):
        message = f"{key} provider failed: {type(err).__name__}: {err}"
        if key == "opencode":
            return {
                "opencode": {"error": message},
                "copilot_cli": {"disabled": True},
            }
        return {"error": message}

    def collect_results():
        result = {}
        fetchers = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            if args.tool in ("codex", "all") and config.get("codex", {}).get("enabled", True):
                fetchers["codex"] = executor.submit(get_codex_data, args, config)
            if args.tool in ("amp", "all"):
                fetchers["amp"] = executor.submit(get_amp_data, args)
            if args.tool in ("antigravity", "all"):
                fetchers["antigravity"] = executor.submit(get_antigravity_data, args)
            if args.tool in ("opencode", "all"):
                fetchers["opencode"] = executor.submit(get_opencode_data, args, config)
            if args.tool == "pi":
                fetchers["pi"] = executor.submit(get_pi_data, args, config)
            if args.tool == "pioneer" or (args.tool == "all" and config.get("pioneer", {}).get("enabled", False)):
                fetchers["pioneer"] = executor.submit(get_pioneer_data, args, config)
            if args.tool == "agentrouter" or (args.tool == "all" and config.get("agentrouter", {}).get("enabled", False)):
                fetchers["agentrouter"] = executor.submit(get_agentrouter_data, args, config)
            if args.tool == "commandcode" or (args.tool == "all" and config.get("commandcode", {}).get("enabled", False)):
                fetchers["commandcode"] = executor.submit(get_commandcode_data, args, config)
            if args.tool == "custom" or (args.tool == "all" and config.get("custom_tools", {}).get("enabled", False)):
                fetchers["custom"] = executor.submit(get_custom_data, args, config)
            for key, fut in fetchers.items():
                try:
                    result[key] = fut.result()
                except Exception as e:
                    result[key] = _provider_error_payload(key, e)
        return result

    def fetch_and_refresh():
        nonlocal sync_codex_pending
        if sync_codex_pending:
            from .providers.codex import refresh_all_accounts
            if not args.json:
                print_c("  ⟲  syncing codex accounts...", "\033[90m", args.no_color)
            refresh_all_accounts(config)
            sync_codex_pending = False
            return collect_results()

        result = collect_results()
        from .providers.codex import refresh_accounts
        stale_names = []
        now = time.monotonic()
        auto_refresh = config.get("codex", {}).get("auto_refresh", True)

        if auto_refresh:
            for acc in result.get("codex", {}).get("accounts", []):
                name = acc.get("name")
                if not name or not any(l.get("is_stale") for l in acc.get("limits", [])):
                    continue
                last_attempt = codex_refresh_attempts.get(name)
                if args.watch and last_attempt is not None and now - last_attempt < codex_refresh_cooldown:
                    continue
                stale_names.append(name)

        if stale_names:
            for name in stale_names:
                codex_refresh_attempts[name] = now
            if not args.json:
                print_c(f"  ⟲  refreshing stale codex accounts: {', '.join(stale_names)}...", "\033[90m", args.no_color)
            refresh_accounts(stale_names, config)
            result = collect_results()
        return result

    # They're loaded lazily so we don't break when recommendations.py
    # or waste_tracker.py are missing (e.g. in isolated test runs).
    from . import recommendations as rec_mod
    from . import waste_tracker

    def _record(result):
        if not args.no_record:
            waste_tracker.record_snapshot(result)

    if args.reset_waste:
        ok = waste_tracker.reset_snapshots()
        if ok:
            print_c(f"  ✓ waste history cleared ({waste_tracker.SNAPSHOT_PATH})", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to delete {waste_tracker.SNAPSHOT_PATH}", "\033[31m", args.no_color)
        return

    if args.record:
        result = fetch_and_refresh()
        _record(result)
        return

    if args.waste:
        # Try to record this run's snapshot first so today's data is included,
        # but never let a fetch failure block the historical report.
        try:
            result = fetch_and_refresh()
            _record(result)
        except Exception as e:
            if not args.json:
                print_c(f"  ⚠ live fetch failed ({type(e).__name__}); showing history only", "\033[33m", args.no_color)
        report = waste_tracker.compute_waste(days=args.days)
        if args.json:
            print(json.dumps(report, indent=2))
            return
        waste_tracker.display_waste_report(report, args.days, args, print_c)
        return

    one_line_tier = next((t for t in ("hard", "quick", "cli") if getattr(args, t)), None)

    if one_line_tier or args.reco:
        result = fetch_and_refresh()
        _record(result)
        recs = rec_mod.compute_recommendations(result, parse_to_utc, fmt_reset)
        if args.json:
            print(json.dumps(recs, indent=2))
            return
        if one_line_tier:
            rec_mod.display_one_line(one_line_tier, recs, args, print_c)
        else:
            rec_mod.display_recommendations(recs, args, print_c)
        return

    def display_result(result):
        recs = None if args.no_recommend else rec_mod.compute_recommendations(result, parse_to_utc, fmt_reset)

        if args.json:
            payload = {}
            for k, v in result.items():
                if k == "opencode" and isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        payload[sub_k] = sub_v
                else:
                    payload[k] = v
            if recs is not None:
                payload["recommendations"] = recs
            print(json.dumps(payload, indent=2))
            return

        print_c(f"\n  AI Tools Status", "\033[1m", args.no_color)
        if args.watch:
            print_c(
                f"  watching every {args.interval:g}s  updated {format_timestamp(datetime.now().astimezone())}",
                "\033[90m",
                args.no_color,
            )
        print("  " + "─" * 52)

        if args.tool == "all" and recs is not None:
            display_at_glance(result, recs, args)

        if any(k in result for k in ("codex", "amp", "antigravity", "pioneer", "agentrouter", "commandcode", "custom")):
            print_c("\n  Quota Left", "\033[1m", args.no_color)

        if "codex" in result:
            display_codex_text(result["codex"], args)
        if "amp" in result:
            display_amp_text(result["amp"], args)
        if "antigravity" in result:
            display_antigravity_text(result["antigravity"], args)
        if "pioneer" in result:
            display_pioneer_text(result["pioneer"], args)
        if "agentrouter" in result:
            display_agentrouter_text(result["agentrouter"], args)
        if "commandcode" in result:
            display_commandcode_text(result["commandcode"], args)
        if "custom" in result:
            display_custom_text(result["custom"], args)
        if "opencode" in result:
            display_opencode_text(result["opencode"], args)
        if "pi" in result:
            display_pi_text(result["pi"], args)

        print("\n  " + "─" * 52)
        if args.watch:
            print_c("  Press Ctrl+C to stop live updates", "\033[90m", args.no_color)
        else:
            print_c(f"  Tip: use --watch for live {tool_label} updates", "\033[90m", args.no_color)
        print()

    if args.watch:
        try:
            while True:
                result = fetch_and_refresh()
                _record(result)
                if not args.json:
                    print("\033[2J\033[H", end="")
                display_result(result)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            if not args.json:
                print()
        return

    result = fetch_and_refresh()
    _record(result)
    display_result(result)
