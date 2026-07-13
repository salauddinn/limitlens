"""
CLI entrypoint — argparse, concurrent dispatch, and main loop.

This module wires together the provider registry, recommendations engine,
and waste tracker into the unified ``limitlens`` command.
"""

import argparse
import json
import platform
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
from .config import (
    is_provider_enabled,
    ConfigValidationError,
    auto_detect_providers,
    reset_custom_tool_spend,
    limitlens_config_path,
)
from .providers import (
    get_codex_data, display_codex_text,
    get_amp_data, display_amp_text,
    get_antigravity_data, display_antigravity_text,
    get_opencode_data, display_opencode_text,
    get_claude_data, display_claude_text,
    get_pi_data, display_pi_text,
    get_kilo_data, display_kilo_text,
    get_pioneer_data, display_pioneer_text,
    get_commandcode_data, display_commandcode_text,
    get_custom_data, display_custom_text,
    get_cursor_data, display_cursor_text,
    get_cline_data, display_cline_text,
    get_grok_data, display_grok_text,
)
from .providers.observed import display_at_glance

from .logging import get_logger as _get_logger
_log = _get_logger("limitlens.cli")


def log_error(e, context=""):
    """Log an exception to the limitlens log file.

    Delegates to the unified logger *and* directly writes to the file so that
    a ``LIMITLENS_LOG_PATH`` env var patched at test time is always honoured
    (the logger singleton is initialised at import time before env patches).
    """
    import os
    import traceback
    _log.exception("%sError: %s: %s", context, type(e).__name__, e)
    # Also write directly to the env-specified path so tests that patch
    # LIMITLENS_LOG_PATH *after* module import still see the output.
    log_path = os.environ.get("LIMITLENS_LOG_PATH")
    if log_path:
        try:
            from datetime import datetime
            log_path = os.path.expanduser(log_path)
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as _f:
                _f.write(f"[{datetime.now().isoformat()}] {context}Error: {type(e).__name__}: {e}\n")
                traceback.print_exc(file=_f)
                _f.write("\n")
        except Exception:
            pass


def _doctor_rows(config):
    import os
    import sys

    from .providers import PROVIDER_DESCRIPTORS
    detected = auto_detect_providers(limitlens_config_path(), write=False, interactive=False)

    def status_for(key, default):
        configured = is_provider_enabled(config, key, default=default)
        found = is_provider_enabled(detected, key, default=False)
        if configured and found:
            return "ready", "ready", "Provider is configured and detected."
        if configured:
            return "enabled_not_detected", "enabled, not detected", "Check local installation, auth, or provider config."
        if found:
            return "detected_disabled", "detected, disabled", "Enable it in LimitLens config if you want to track it."
        return "not_configured", "not configured", "Configure this provider only if you use it."

    rows = [
        {
            "key": desc.key,
            "label": desc.label,
            **dict(zip(
                ("state", "status", "next_step"),
                status_for(desc.config_key, desc.default_enabled),
            )),
        }
        for desc in PROVIDER_DESCRIPTORS.values()
        # copilot_cli is internal and not surfaced in doctor
        if desc.key != "copilot_cli"
    ]
    rows.append({
        "key": "menubar",
        "label": "Menubar",
        "state": "available" if sys.platform == "darwin" else "macos_only",
        "status": "available" if sys.platform == "darwin" else "macOS only",
        "next_step": "Run `limitlens-menubar` on macOS." if sys.platform == "darwin" else "Use the CLI on non-macOS systems.",
    })
    widget_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "iterm_widget.py")
    rows.append({
        "key": "iterm_widget",
        "label": "iTerm widget",
        "state": "available" if os.path.exists(widget_path) else "not_installed",
        "status": "available" if os.path.exists(widget_path) else "not installed",
        "next_step": "Enable the bundled iTerm2 status-bar script." if os.path.exists(widget_path) else "Install the bundled iTerm2 script if you use iTerm2.",
    })
    return rows




def _doctor_report(rows):
    try:
        from . import __version__ as version
    except ImportError:
        from importlib.metadata import version as _pkg_version
        version = _pkg_version("limitlens")

    return {
        "version": 1,
        "limitlens_version": version,
        "os": platform.system() or "unknown",
        "python": platform.python_version(),
        "providers": {
            row["key"]: {
                "label": row["label"],
                "state": row["state"],
                "status": row["status"],
                "next_step": row["next_step"],
            }
            for row in rows
        },
        "privacy": "Sanitized report: no tokens, cookies, emails, config values, or local paths included.",
        "next": "Run `limitlens` for the dashboard or `limitlens suggest` for routing advice.",
    }


def _display_doctor(config, report=False):
    rows = _doctor_rows(config)

    if report:
        print(json.dumps(_doctor_report(rows), indent=2, sort_keys=True))
        return

    width = max(len(row["label"]) for row in rows)
    print("LimitLens Doctor\n")
    for row in rows:
        print(f"{row['label']:<{width}}  {row['status']}")
    print("\nNext: run `limitlens` for the dashboard or `limitlens suggest` for routing advice.")

def _run_subcommand(argv):
    parser = argparse.ArgumentParser(
        prog="limitlens run",
        description="Launch the best available AI agent CLI for a natural-language task.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  limitlens run "Plan the auth refactor"
  limitlens run "Fix the failing tests"
  limitlens run --tool agy "Build the dashboard"
  limitlens run --dry-run "Research the migration path"
""",
    )
    parser.add_argument("--tool", choices=["auto", "pi", "kilo", "agy", "antigravity", "amp", "codex", "opencode", "cline", "commandcode", "cmd"], default="auto", help="Force a tool instead of auto-routing")
    parser.add_argument("--dry-run", action="store_true", help="Show the chosen tool and command without launching it")
    parser.add_argument("--cwd", help="Working directory for the launched agent")
    parser.add_argument("--plain", action="store_true", help="Plain output: no color")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    parser.add_argument("--debug", "-d", action="store_true", help="Show runner traceback on errors")
    parser.add_argument("prompt", nargs=argparse.REMAINDER, help="Task prompt to route")
    args = parser.parse_args(argv)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.error("prompt is required")
    if args.plain:
        args.no_color = True

    try:
        from .runner import run_task

        config = load_limitlens_config()
        preferred = None if args.tool == "auto" else args.tool
        code = run_task(
            prompt,
            config=config,
            preferred_tool=preferred,
            dry_run=args.dry_run,
            cwd=args.cwd,
            no_color=args.no_color,
            require_executable=not args.dry_run,
        )
    except Exception as e:
        import sys
        import traceback
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        else:
            print_c(f"  ⚠ runner failed: {e}", "\033[31m", args.no_color)
        log_error(e, "Runner ")
        raise SystemExit(1)

    if code:
        raise SystemExit(code)


def _main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        _run_subcommand(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(
        description="Unified status checker for Codex, Amp, Antigravity, OpenCode, Pi, Cursor, and more",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Common commands:
  limitlens run "Build a feature"   Launch the best AI agent CLI
  limitlens doctor                  Check local provider readiness
  limitlens suggest                 Which AI should I use now?
  limitlens usage                   Show day-wise usage
  limitlens all                     Show hidden/empty providers too
  limitlens watch                   Refresh continuously
""",
    )
    try:
        from . import __version__ as _ver
    except ImportError:
        from importlib.metadata import version as _pkg_version
        _ver = _pkg_version("limitlens")
    parser.add_argument("--version", action="version", version=f"limitlens {_ver}")
    parser.add_argument("command", nargs="?", choices=["suggest", "s", "usage", "u", "all", "a", "watch", "w", "doctor", "d"], help=argparse.SUPPRESS)
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode and print full traceback to stderr")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")
    parser.add_argument("--report", action="store_true", help="Print a sanitized doctor report as JSON")
    parser.add_argument("--plain", action="store_true", help="Plain output: no color and fewer decorations")
    parser.add_argument("--no-color", action="store_true", help="Disable color output")
    parser.add_argument("--redact", action="store_true", default=True, help="Redact PII like emails and account paths (default: True)")
    parser.add_argument("--no-redact", action="store_false", dest="redact", help="Show PII without redaction")
    parser.add_argument("--tool", choices=["codex", "amp", "antigravity", "opencode", "pi", "kilo", "claude", "pioneer", "commandcode", "custom", "cursor", "cline", "grok", "all"], default="all", help="Check specific tool")
    parser.add_argument("-w", "--watch", action="store_true", help="Refresh continuously for live status updates")
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds when using --watch (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Show detailed rows and low-level warnings")
    parser.add_argument("--sync-codex", action="store_true", help="Refresh all Codex accounts before showing status")
    parser.add_argument("--refresh-codex", action="store_true", help="Refresh all discovered Codex accounts and exit (no status output)")
    parser.add_argument("-a", "--all", action="store_true", help="Show all limits, bypassing auto-hide rules")
    parser.add_argument("--no-recommend", action="store_true", help="Skip the recommendation block")
    parser.add_argument("--reco", action="store_true", help="Print only the recommendation block (skip full status)")
    parser.add_argument("--hard", action="store_true", help="One-line recommendation for hard tasks (multi-file, refactor)")
    parser.add_argument("--quick", action="store_true", help="One-line recommendation for quick edits / grunt work")
    parser.add_argument("--cli", action="store_true", help="One-line recommendation for CLI / scripting / pair-prog")
    parser.add_argument("--waste", action="store_true", help="Show waste report (%% of quota wasted at reset over last N days)")
    parser.add_argument("-u", "--usage", action="store_true", help="Show usage tracking history")
    parser.add_argument("--export-usage", type=str, help="Export usage tracking history to JSON file")
    parser.add_argument("--import-usage", type=str, help="Import usage tracking history from JSON file")
    parser.add_argument("--days", type=int, default=7, help="Window for --waste report (default: 7)")
    parser.add_argument("--record", action="store_true", help="Quietly record a snapshot for waste tracking, then exit")
    parser.add_argument("--no-record", action="store_true", help="Skip snapshot recording on this run")
    parser.add_argument("--reset-waste", action="store_true", help="Delete all recorded waste snapshots, then exit")
    parser.add_argument("--reset-spend", action="store_true", help="Reset observed spend tracking (Pi, OpenCode, Copilot CLI, and manual custom tools)")
    parser.add_argument("--init-config", action="store_true", help="Detect installed tools and write an initial config file, then exit")
    parser.add_argument("--store-token", metavar="PROVIDER", help="Securely store an API token in the OS keychain (e.g. pioneer, commandcode). Prompts for token securely.")
    parser.add_argument("--store-token-stdin", metavar="PROVIDER", help="Securely store an API token in the OS keychain reading from stdin")
    args = parser.parse_args()
    if args.command in ("suggest", "s"):
        args.reco = True
    elif args.command in ("usage", "u"):
        args.usage = True
    elif args.command in ("all", "a"):
        args.all = True
    elif args.command in ("watch", "w"):
        args.watch = True
    if args.report and args.command not in ("doctor", "d"):
        parser.error("--report can only be used with `limitlens doctor`")
        return
    if args.plain:
        args.no_color = True
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")

    if args.init_config:
        path = limitlens_config_path()
        auto_detect_providers(path, write=True, interactive=True)
        return

    tool_label = {
        "codex": "Codex quota",
        "amp": "Amp API",
        "antigravity": "Antigravity",
        "opencode": "observed usage",
        "pi": "Pi sessions",
        "claude": "Claude Code usage",
        "pioneer": "Pioneer team quota",
        "commandcode": "CommandCode credits",
        "custom": "custom tools",
        "cursor": "Cursor usage",
        "cline": "Cline CLI",
        "grok": "Grok status",
        "all": "AI tool",
    }[args.tool]
    config = load_limitlens_config()

    if args.command in ("doctor", "d"):
        _display_doctor(config, report=args.report)
        return

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
    refresh_metadata = {}

    def _provider_error_payload(key, err):
        message = f"{key} provider failed: {type(err).__name__}: {err}"
        if key == "opencode":
            return {
                "opencode": {"error": message},
                "copilot_cli": {"disabled": True},
            }
        return {"error": message}

    def collect_results():
        import sys as _sys
        from .providers import PROVIDER_DESCRIPTORS
        result = {}

        # Map each provider key to the function imported at cli.py module level.
        # Using the module's __dict__ ensures that test patches (e.g.
        # @patch("limitlens.cli.get_codex_data")) are honoured (#5 / testability).
        _cli_module = _sys.modules[__name__]
        _fetch_by_key = {
            "codex":       getattr(_cli_module, "get_codex_data", None),
            "amp":         getattr(_cli_module, "get_amp_data", None),
            "antigravity": getattr(_cli_module, "get_antigravity_data", None),
            "opencode":    getattr(_cli_module, "get_opencode_data", None),
            "pi":          getattr(_cli_module, "get_pi_data", None),
            "kilo":        getattr(_cli_module, "get_kilo_data", None),
            "claude":      getattr(_cli_module, "get_claude_data", None),
            "copilot_cli": None,  # fetched via opencode provider
            "cursor":      getattr(_cli_module, "get_cursor_data", None),
            "cline":       getattr(_cli_module, "get_cline_data", None),
            "pioneer":     getattr(_cli_module, "get_pioneer_data", None),
            "commandcode": getattr(_cli_module, "get_commandcode_data", None),
            "custom":      getattr(_cli_module, "get_custom_data", None),
            "grok":        getattr(_cli_module, "get_grok_data", None),
        }

        # Determine which providers to fetch — data-driven from the registry.
        # Uses descriptor.default_enabled as the single source of truth (#5 / #6).
        active_descs = []
        for desc in PROVIDER_DESCRIPTORS.values():
            if desc.key == "copilot_cli":
                # copilot_cli is fetched as a side-effect of opencode; skip here
                continue
            if args.tool == desc.key or any(args.tool == a for a in desc.aliases):
                active_descs.append(desc)
            elif args.tool == "all" and is_provider_enabled(
                config, desc.config_key, default=desc.default_enabled
            ):
                active_descs.append(desc)

        enabled_count = len(active_descs)
        max_workers = max(16, enabled_count)

        def _call_fetch(desc, args, config):
            """Call the provider's fetch function with the right signature."""
            fn = _fetch_by_key.get(desc.key) or desc.fetch
            try:
                return fn(args, config)
            except TypeError:
                # Some providers only accept (args) e.g. get_amp_data
                try:
                    return fn(args)
                except TypeError:
                    # Some providers only accept (config) e.g. get_copilot_cli_usage
                    return fn(config)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            fetchers = {
                desc.key: executor.submit(_call_fetch, desc, args, config)
                for desc in active_descs
            }
            for key, fut in fetchers.items():
                try:
                    result[key] = fut.result()
                except Exception as e:
                    import sys
                    import traceback
                    if getattr(args, "debug", False):
                        traceback.print_exc(file=sys.stderr)
                    log_error(e, f"Provider {key} ")
                    result[key] = _provider_error_payload(key, e)
        return result



    def fetch_and_refresh():
        nonlocal sync_codex_pending, refresh_metadata
        if sync_codex_pending:
            from .providers.codex import refresh_all_accounts
            if not args.json:
                print_c("  ⟲  syncing codex accounts...", "\033[90m", args.no_color)
            results = refresh_all_accounts(config)
            refresh_metadata["codex"] = {
                "mode": "sync_all",
                "results": results,
            }
            sync_codex_pending = False
            result = collect_results()
            result["_refresh"] = refresh_metadata
            return result

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
            results = refresh_accounts(stale_names, config)
            refresh_metadata["codex"] = {
                "mode": "stale_accounts",
                "accounts": stale_names,
                "results": results,
            }
            result = collect_results()
        if refresh_metadata:
            result["_refresh"] = refresh_metadata
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


    if args.store_token or getattr(args, "store_token_stdin", None):
        provider = args.store_token or args.store_token_stdin
        try:
            if getattr(args, "store_token_stdin", None):
                import sys
                token = sys.stdin.read().strip()
            else:
                import getpass
                token = getpass.getpass(f"Enter token for {provider}: ").strip()

            if not token:
                print_c(f"  ⚠ No token provided for '{provider}'.", "\033[31m", getattr(args, "no_color", False))
                return

            from .keychain import set_keychain_token
            if set_keychain_token(provider, token):
                print_c(f"  ✓ Token for '{provider}' stored securely in keychain.", "\033[32m", args.no_color)
            else:
                print_c(f"  ⚠ Failed to store token for '{provider}' in keychain.", "\033[31m", args.no_color)
        except Exception as e:
            import sys
            import traceback
            if getattr(args, "debug", False):
                traceback.print_exc(file=sys.stderr)
            log_error(e, "Store token ")
            print_c(f"  ⚠ Error storing token: {e}", "\033[31m", args.no_color)
        return

    if args.export_usage:
        ok = usage_tracker.export_usage(args.export_usage)
        if ok:
            print_c(f"  ✓ usage exported to {args.export_usage}", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to export usage to {args.export_usage}", "\033[31m", args.no_color)
        if not (args.usage or getattr(args, "run", False) or args.waste):
            return

    if args.import_usage:
        ok = usage_tracker.import_usage(args.import_usage)
        if ok:
            print_c(f"  ✓ usage imported from {args.import_usage}", "\033[32m", args.no_color)
        else:
            print_c(f"  ⚠ failed to import usage from {args.import_usage}", "\033[31m", args.no_color)
        if not (args.usage or getattr(args, "run", False) or args.waste):
            return

    if args.usage:
        if getattr(args, "days", None):
            for k in ["opencode", "pi", "copilot_cli", "claude"]:
                if k not in config:
                    config[k] = {}
                config[k]["days"] = [args.days]

        result = {}
        try:
            result = fetch_and_refresh()
            _record(result)
        except Exception as e:
            import sys
            import traceback
            if getattr(args, "debug", False):
                traceback.print_exc(file=sys.stderr)
            log_error(e, "Usage fetch ")
            if not args.json:
                print_c(f"  ⚠ live fetch failed ({type(e).__name__}); showing history only", "\033[33m", args.no_color)

        data = {}
        opencode_result = result.get("opencode")
        if isinstance(opencode_result, dict) and any(k in opencode_result for k in ("opencode", "pi", "copilot_cli", "claude")):
            data.update(opencode_result)
        elif isinstance(opencode_result, dict):
            data["opencode"] = opencode_result
        if isinstance(result.get("pi"), dict) and "pi" not in data:
            data["pi"] = result["pi"]
        if isinstance(result.get("copilot_cli"), dict) and "copilot_cli" not in data:
            data["copilot_cli"] = result["copilot_cli"]
        if isinstance(result.get("claude"), dict) and "claude" not in data:
            data["claude"] = result["claude"]

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

        # Reset any manual 'used' and 'request_count' fields in custom_tools inside config.json
        try:
            if reset_custom_tool_spend(limitlens_config_path()):
                print_c("  ✓ custom tools usage reset in config.json", "\033[32m", args.no_color)
        except ConfigValidationError as e:
            print_c(f"  ⚠ failed to update custom tools: {e}", "\033[31m", args.no_color)

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
            payload["schema_version"] = 1
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

        if any(k in result for k in ("codex", "amp", "antigravity", "pi", "kilo", "pioneer", "commandcode", "custom", "cursor", "cline", "grok")):
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
        if "commandcode" in result:
            display_commandcode_text(result["commandcode"], args)
        if "custom" in result:
            display_custom_text(result["custom"], args)
        if "cursor" in result:
            display_cursor_text(result["cursor"], args)
        if "cline" in result:
            display_cline_text(result["cline"], args)
        if "grok" in result:
            display_grok_text(result["grok"], args)

        if "opencode" in result:
            display_opencode_text(result["opencode"], args)
        else:
            if "claude" in result:
                display_claude_text(result["claude"], args)
            if "pi" in result:
                display_pi_text(result["pi"], args)
            if "kilo" in result:
                display_kilo_text(result["kilo"], args)

        # Removed bottom border
        if args.watch:
            print_c("  ⟲  Press Ctrl+C to stop", "\033[90m", args.no_color)
        else:
            print_c(f"  💡 Tip: use --watch for live {tool_label} updates", "\033[90m", args.no_color)
        print()

    if args.watch:
        _prev_lines = [0]

        def _clear_watch():
            n = _prev_lines[0]
            if n > 0:
                print(f"\033[{n}A\033[J", end="", flush=True)

        try:
            while True:
                result = fetch_and_refresh()
                _record(result)
                if not args.json:
                    import io as _io
                    import sys as _sys
                    _clear_watch()
                    buf = _io.StringIO()
                    _orig = _sys.stdout
                    _sys.stdout = buf
                    try:
                        display_result(result)
                    finally:
                        _sys.stdout = _orig  # always restore, even if display_result raises
                    output = buf.getvalue()
                    print(output, end="", flush=True)
                    _prev_lines[0] = output.count("\n")
                else:
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
        import traceback
        debug_enabled = "--debug" in sys.argv or "-d" in sys.argv
        no_color_enabled = "--no-color" in sys.argv or "--plain" in sys.argv
        if debug_enabled:
            traceback.print_exc(file=sys.stderr)
        elif no_color_enabled:
            print("\n  [LimitLens] An unexpected error occurred.")
            print(f"  Details: {e}")
            print("  If this persists, please open an issue on GitHub.\n")
        else:
            print("\n  \033[31m[LimitLens] An unexpected error occurred.\033[0m")
            print(f"  \033[90mDetails:\033[0m {e}")
            print("  \033[90mIf this persists, please open an issue on GitHub.\033[0m\n")
        log_error(e, "Top-level ")
        sys.exit(1)
