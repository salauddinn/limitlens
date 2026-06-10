"""
CLI entrypoint — argparse, concurrent dispatch, and main loop.

This module wires together the provider registry, recommendations engine,
and waste tracker into the unified ``limitlens`` command.
"""

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

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
    get_cursor_data, display_cursor_text,
)
from .providers.observed import display_at_glance
from .providers.agentrouter import is_agentrouter_enabled

def _main():
    parser = argparse.ArgumentParser(description="Unified status checker for Codex, Amp, Antigravity, OpenCode, Pi, AgentRouter, Cursor, and more")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    parser.add_argument("--redact", action="store_true", default=True, help="Redact PII like emails and account paths (default: True)")
    parser.add_argument("--no-redact", action="store_false", dest="redact", help="Show PII without redaction")
    parser.add_argument("--tool", choices=["codex", "amp", "antigravity", "opencode", "pi", "pioneer", "agentrouter", "commandcode", "custom", "cursor", "all"], default="all", help="Check specific tool")
    parser.add_argument("--watch", action="store_true", help="Refresh continuously for live status updates")
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds when using --watch (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Show detailed rows and low-level warnings")
    parser.add_argument("--sync-codex", action="store_true", help="Refresh all Codex accounts before showing status")
    parser.add_argument("--refresh-codex", action="store_true", help="Refresh all discovered Codex accounts and exit (no status output)")
    parser.add_argument("--all", action="store_true", help="Show all limits, bypassing auto-hide rules")
    parser.add_argument("--no-recommend", action="store_true", help="Skip the recommendation block")
    parser.add_argument("--reco", action="store_true", help="Print only the recommendation block (skip full status)")
    parser.add_argument("--hard", action="store_true", help="One-line recommendation for hard tasks (multi-file, refactor)")
    parser.add_argument("--quick", action="store_true", help="One-line recommendation for quick edits / grunt work")
    parser.add_argument("--cli", action="store_true", help="One-line recommendation for CLI / scripting / pair-prog")
    parser.add_argument("--waste", action="store_true", help="Show waste report (%% of quota wasted at reset over last N days)")
    parser.add_argument("--usage", action="store_true", help="Show usage tracking history")
    parser.add_argument("--export-usage", type=str, help="Export usage tracking history to JSON file")
    parser.add_argument("--import-usage", type=str, help="Import usage tracking history from JSON file")
    parser.add_argument("--days", type=int, default=7, help="Window for --waste report (default: 7)")
    parser.add_argument("--record", action="store_true", help="Quietly record a snapshot for waste tracking, then exit")
    parser.add_argument("--no-record", action="store_true", help="Skip snapshot recording on this run")
    parser.add_argument("--reset-waste", action="store_true", help="Delete all recorded waste snapshots, then exit")
    parser.add_argument("--reset-spend", action="store_true", help="Reset observed spend tracking (Pi, OpenCode, Copilot CLI, and Kilo Code via configured providers)")
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
        "agentrouter": "Kilo Code (AgentRouter)",
        "commandcode": "Command Code",
        "custom": "custom tool",
        "cursor": "Cursor",
        "all": "AI tool",
    }[args.tool]
    config = load_limitlens_config()

    if args.refresh_codex:
        from .providers.codex import refresh_all_accounts
        if not args.json:
            print_c("  ⟲  refreshing all codex accounts...", "\033[90m", args.no_color)
        results = refresh_all_accounts(config)
        if args.json:
            print(json.dumps(results, indent=2))
            return
        if not results:
            print_c("  ⚠ no codex accounts discovered", "\033[33m", args.no_color)
            return
        for name, res in results.items():
            if res.get("ok"):
                print_c(f"  ✓ {name} refreshed", "\033[32m", args.no_color)
            else:
                print_c(f"  ⚠ {name} failed: {res.get('error')}", "\033[31m", args.no_color)
        return

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
            if args.tool == "codex" or (args.tool == "all" and str(config.get("codex", {}).get("enabled", True)).lower() not in ("false", "0", "no")):
                fetchers["codex"] = executor.submit(get_codex_data, args, config)
            if args.tool == "amp" or (args.tool == "all" and str(config.get("amp", {}).get("enabled", True)).lower() not in ("false", "0", "no")):
                fetchers["amp"] = executor.submit(get_amp_data, args)
            if args.tool == "antigravity" or (args.tool == "all" and str(config.get("antigravity", {}).get("enabled", True)).lower() not in ("false", "0", "no")):
                fetchers["antigravity"] = executor.submit(get_antigravity_data, args)
            if args.tool == "opencode" or (args.tool == "all" and str(config.get("opencode", {}).get("enabled", True)).lower() not in ("false", "0", "no")):
                fetchers["opencode"] = executor.submit(get_opencode_data, args, config)
            if args.tool == "pi" or (args.tool == "all" and str(config.get("pi", {}).get("enabled", False)).lower() not in ("false", "0", "no")):
                fetchers["pi"] = executor.submit(get_pi_data, args, config)
            if args.tool == "pioneer" or (args.tool == "all" and str(config.get("pioneer", {}).get("enabled", False)).lower() not in ("false", "0", "no")):
                fetchers["pioneer"] = executor.submit(get_pioneer_data, args, config)
            if args.tool == "agentrouter" or (args.tool == "all" and is_agentrouter_enabled(config)):
                fetchers["agentrouter"] = executor.submit(get_agentrouter_data, args, config)
            if args.tool == "commandcode" or (args.tool == "all" and str(config.get("commandcode", {}).get("enabled", False)).lower() not in ("false", "0", "no")):
                fetchers["commandcode"] = executor.submit(get_commandcode_data, args, config)
            if args.tool == "custom" or (args.tool == "all" and str(config.get("custom_tools", {}).get("enabled", False)).lower() not in ("false", "0", "no")):
                fetchers["custom"] = executor.submit(get_custom_data, args, config)
            if args.tool == "cursor" or (args.tool == "all" and str(config.get("cursor", {}).get("enabled", True)).lower() not in ("false", "0", "no")):
                fetchers["cursor"] = executor.submit(get_cursor_data, args, config)
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
                if not name or not any(lim.get("is_stale") for lim in acc.get("limits", [])):
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
    from . import usage_tracker

    def _record(result):
        if not args.no_record:
            waste_tracker.record_snapshot(result)
            usage_tracker.record_usage(result)


    if args.export_usage:
        ok = usage_tracker.export_usage(args.export_usage)
        if ok:
            print_c(f"  ✓ usage exported to {args.export_usage}", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to export usage to {args.export_usage}", "\033[31m", args.no_color)
        return

    if args.import_usage:
        ok = usage_tracker.import_usage(args.import_usage)
        if ok:
            print_c(f"  ✓ usage imported from {args.import_usage}", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to import usage from {args.import_usage}", "\033[31m", args.no_color)
        return

    if args.usage:
        if getattr(args, "days", None):
            for k in ["opencode", "pi", "copilot_cli"]:
                if k not in config:
                    config[k] = {}
                config[k]["days"] = [args.days]

        result = {}
        try:
            result = fetch_and_refresh()
            _record(result)
        except Exception as e:
            if not args.json:
                print_c(f"  ⚠ live fetch failed ({type(e).__name__}); showing history only", "\033[33m", args.no_color)

        data = {}
        opencode_result = result.get("opencode")
        if isinstance(opencode_result, dict) and any(k in opencode_result for k in ("opencode", "pi", "copilot_cli")):
            data.update(opencode_result)
        elif isinstance(opencode_result, dict):
            data["opencode"] = opencode_result
        if isinstance(result.get("pi"), dict) and "pi" not in data:
            data["pi"] = result["pi"]
        if isinstance(result.get("copilot_cli"), dict) and "copilot_cli" not in data:
            data["copilot_cli"] = result["copilot_cli"]

        analytics = usage_tracker.compute_usage_analytics(args.days, observed=data, config=config)
        if args.json:
            payload = dict(analytics)
            payload["version"] = 4
            payload["consolidated_usage"] = {
                key: item["used"] for key, item in analytics["snapshot_usage"].items()
            }
            print(json.dumps(payload, indent=2))
        else:
            usage_tracker.display_consolidated_report(args, print_c, analytics=analytics)
        return
    if args.reset_waste:
        ok = waste_tracker.reset_snapshots()
        if ok:
            print_c(f"  ✓ waste history cleared ({waste_tracker.SNAPSHOT_PATH})", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to delete {waste_tracker.SNAPSHOT_PATH}", "\033[31m", args.no_color)
        return

    if args.reset_spend:
        from .providers.observed import mark_spend_reset
        extra_data = {}

        # If Kilo is configured to use AgentRouter locally, capture the raw
        # gateway totals as the reset baseline.
        if is_agentrouter_enabled(config):
            import limitlens.providers.agentrouter as ar
            ar_data = ar.get_agentrouter_data(args, config, apply_reset_offset=False)
            if "error" in ar_data or not ar_data.get("tiers"):
                print_c("  ⚠ failed to capture AgentRouter/Kilo reset baseline; clearing previous baseline", "\033[33m", args.no_color)
                if "error" in ar_data:
                    print_c(f"    Details: {ar_data['error']}", "\033[90m", args.no_color)
                extra_data["agentrouter_offset"] = None
            else:
                tier = ar_data["tiers"][0]
                extra_data["agentrouter_offset"] = {
                    "used": tier["used"],
                    "request_count": ar_data.get("request_count", 0),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        # Reset any manual 'used' and 'request_count' fields in custom_tools inside config.json
        from .config import limitlens_config_path
        import os
        import tempfile
        config_path = limitlens_config_path()
        config_updated = False
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                if not isinstance(user_config, dict):
                    user_config = {}

                if "custom_tools" in user_config and "tools" in user_config["custom_tools"]:
                    tools_cfg = user_config["custom_tools"]["tools"]
                    tools_list = []
                    if isinstance(tools_cfg, dict):
                        tools_list = tools_cfg.values()
                    elif isinstance(tools_cfg, list):
                        tools_list = tools_cfg

                    for tool_data in tools_list:
                        if isinstance(tool_data, dict):
                            used_val = tool_data.get("used", 0)
                            if isinstance(used_val, (int, float)) and used_val > 0:
                                tool_data["used"] = 0
                                config_updated = True
                            elif isinstance(used_val, str):
                                try:
                                    if float(used_val) > 0:
                                        tool_data["used"] = 0
                                        config_updated = True
                                except ValueError:
                                    pass

                            req_val = tool_data.get("request_count", 0)
                            if isinstance(req_val, (int, float)) and req_val > 0:
                                tool_data["request_count"] = 0
                                config_updated = True
                            elif isinstance(req_val, str):
                                try:
                                    if float(req_val) > 0:
                                        tool_data["request_count"] = 0
                                        config_updated = True
                                except ValueError:
                                    pass

                if config_updated:
                    dir_path = os.path.dirname(config_path)
                    os.makedirs(dir_path, exist_ok=True)
                    fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix="config_", suffix=".tmp")
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(user_config, f, indent=2)
                    os.replace(tmp_path, config_path)
                    print_c("  ✓ custom tools usage reset in config.json", "\033[32m", args.no_color)
            except (json.JSONDecodeError, OSError):
                print_c(f"  ⚠ failed to update custom tools in {config_path}", "\033[31m", args.no_color)

        if mark_spend_reset(extra_data=extra_data):
            print_c("  ✓ spend tracking reset. Future reports will only count spend from now on.", "\033[32m", args.no_color)
        else:
            print_c("  ⚠ failed to record spend reset", "\033[31m", args.no_color)
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
        report = waste_tracker.compute_waste(days=args.days, config=config)
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

        if args.watch:
            ts = format_timestamp(datetime.now().astimezone())
            print_c(f"\n  AI Tools Status  ·  {ts}", "\033[1m", args.no_color)
            print_c(f"  live · refreshing every {args.interval:g}s", "\033[90m", args.no_color)
        else:
            print_c("\n  AI Tools Status", "\033[1m", args.no_color)

        if args.tool == "all" and recs is not None:
            display_at_glance(result, recs, args)

        if any(k in result for k in ("codex", "amp", "antigravity", "pi", "pioneer", "agentrouter", "commandcode", "custom", "cursor")):
            print()
            print_c("  ═══ Quota Left ═══", "\033[1;36m", args.no_color)

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
        if "cursor" in result:
            display_cursor_text(result["cursor"], args)

        if "opencode" in result:
            display_opencode_text(result["opencode"], args)
        elif "pi" in result:
            display_pi_text(result["pi"], args)

        # Removed bottom border
        if args.watch:
            print_c("  ⟲  Press Ctrl+C to stop", "\033[90m", args.no_color)
        else:
            print_c(f"  💡 Tip: use --watch for live {tool_label} updates", "\033[90m", args.no_color)
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

def main():
    """Main execution wrapper to gracefully handle top-level exceptions."""
    try:
        _main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import sys
        print("\n  \033[31m[LimitLens] An unexpected error occurred.\033[0m")
        print(f"  \033[90mDetails:\033[0m {e}")
        print("  \033[90mIf this persists, please open an issue on GitHub.\033[0m\n")
        sys.exit(1)
