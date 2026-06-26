# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.6.1] - 2026-06-26

### Fixed
- Queue menubar refresh requests instead of dropping manual refresh clicks while another refresh is already running.
- Preserve manual refresh intent so queued refreshes still perform the stronger Codex sync.
- Show an in-progress menubar title while refresh is running and include a last-refreshed timestamp in populated menus.

## [1.6.0] - 2026-06-26

### Added
- Add `limitlens run "..."`, a quota-aware launcher that chooses and starts the best available AI CLI for a task.
- Add `--init-config` to explicitly write a starter config from detected local tools.
- Add configurable runner commands, prompt modes, ignored tools, and dry-run previews.

### Changed
- Make zero-config provider detection read-only during normal status, suggest, usage, and runner commands.
- Document production launcher defaults for Pi, Antigravity CLI, Amp, Codex, OpenCode, and Command Code.

### Fixed
- Prevent read-only commands and `limitlens run --dry-run` from creating or rewriting `config.json` unexpectedly.
- Harden runner launch defaults so Amp receives prompts on stdin, Antigravity uses `--prompt-interactive`, and OpenCode uses `run`.
- Reject invalid runner `prompt_mode` values with a clear error instead of silently falling back.

## [1.5.0] - 2026-06-21

### Added
- Add common command aliases: `suggest`/`s`, `usage`/`u`, `all`/`a`, and `watch`/`w`.
- Add low-conflict short flags: `-u`, `-a`, and `-w`.
- Add `--plain` for simpler human-readable output with no color and fewer decorations.
- Add day-wise usage reporting and a `daily` JSON field for snapshot-derived usage.

### Changed
- Improve `--reco` and `suggest` output so it directly recommends tools for hard tasks, quick edits, and CLI work.
- Label snapshot-derived daily usage as approximate because it is based on observed quota decreases.
- Make config reset writes safer with timestamped backups and atomic JSON updates.

### Fixed
- Add regression coverage so provider errors remain visible and directly requested providers still run even when disabled in the all-dashboard config.

## [1.4.0] - 2026-06-14

### Added
- Add `individual_credits` configuration option for Amp to selectively hide credit-only tiers.

## [1.3.3] - 2026-06-14

### Added
- Add Claude Code local session usage tracking.
- Add secure OS keychain token storage for Pioneer and Command Code.
- Add interactive `limitlens-switch` tool launcher.
- Add first-run provider auto-detection and an operations runbook.
- Add time-to-exhaustion metadata to waste reports.

### Changed
- Make menubar refresh and notification thresholds configurable.
- Use consistent deterministic tool icons across CLI, menubar, and iTerm widget.
- Improve Antigravity model display by grouping model families and labeling 5-hour vs weekly limits.

### Security
- Fixed a race condition where temporary files were momentarily readable by other users before `os.chmod` was applied. `usage_tracker`, `waste_tracker`, and `antigravity` now use `tempfile.mkstemp` to create files securely with `0o600` permissions.
- Restrict Antigravity TLS-verification fallback to localhost endpoints and log security warnings.
- Avoid token exposure in process arguments by prompting for `--store-token` values or accepting them via `--store-token-stdin`.

### Fixed
- Fixed a `KeyError` in `config.apply_env_overrides` when custom provider keys (like `pioneer.plan`) were used in the config but missing from `DEFAULT_CONFIG`.
- Allow custom `model_parents` mappings in observed-usage provider configs.
- Respect ignored Antigravity accounts in the switcher.
- Include top-level Claude usage in `--usage --tool claude` analytics.
- Make the iTerm widget gracefully fall back when shared icon helpers are not importable.

## [1.3.2] - 2026-06-10

### Added
- Add `--refresh-codex` to refresh all discovered Codex accounts and exit without printing status
- Add `--reset-spend` for a non-destructive visual spend reset across observed usage providers
- Track observed Amp dollar spend in snapshots and `--usage` reports
- Support Merlin-backed Command Code status endpoints with alternate headers and usage parsing

### Changed
- Menubar "Refresh Now" now forces a full Codex account sync (via `--sync-codex`) so manual refreshes pull fresh Codex data
- Treat spend resets as timestamp cutoffs for Pi/OpenCode/Copilot CLI and as offset subtraction for Kilo Code when it is explicitly configured to use AgentRouter
- Reset manual `custom_tools` usage counters in local config without deleting any underlying provider history

### Fixed
- Respect ignored Codex accounts in historical usage and waste reports
- Detect waste events correctly across report window boundaries
- Avoid double-counting Command Code monthly sub-buckets
- Preserve legacy usage history in exports while avoiding snapshot/history double-counting on import
- Display Amp missed-refill waste as dollar estimates instead of percent-unused reset waste
- Sanitize Command Code environment-derived request headers before API calls

## [1.2.3] - 2026-06-04

### Fixed
- Sanitize AgentRouter auth-related environment headers before API requests
- Add Windows CI coverage and package classifier to match documented support
- Keep the CI matrix running after a single job failure so release failures are diagnosable

## [1.2.2] - 2026-06-04

### Fixed
- Avoid duplicate cache directory setup during snapshot pruning so CI and first-run snapshot recording remain stable

## [1.2.1] - 2026-06-04

### Fixed
- Prevent `limitlens-menubar` from crashing at import time on non-macOS systems without `rumps`
- Fix Windows-safe SQLite read-only URI construction for local database providers
- Close Cursor SQLite connections on all exception paths
- Invoke snapshot retention pruning and restrict cache file permissions for local usage/waste data
- Replace padded menubar dropdown tables with delimiter-based rows that render correctly in proportional macOS menu fonts
- Harden JSONL parsing against non-object malformed records
- Fall back to manual provider data when Pioneer or Command Code APIs return invalid JSON
- Block unexpected redirects and validate web URL schemes before sending provider auth headers
- Align internal package version and dev dependency bounds

## [1.2.0] - 2026-06-04

### Added
- Add native macOS menubar app with low-quota desktop notifications, refresh, and quit actions
- Add compact iTerm2 status widget with resilient registration/retry behavior
- Add provider support for Cursor, Pi local session usage, Pioneer, AgentRouter/Kilo Code, Command Code, and custom quota tools
- Add config-driven provider enable/disable controls and richer example configuration
- Add local usage tracking with JSON import/export and dynamic waste reporting from snapshots
- Add automated install/uninstall scripts and documentation for data storage/privacy
- Add smart tool icons, traffic-light quota indicators, and compact menubar/widget summaries

### Improved
- Redesign README with updated screenshots and clearer install/privacy guidance
- Improve smart recommendations with actionable/expiring slots and all-candidate metadata
- Improve menubar dropdown with a rich usage overview and best-available recommendations
- Improve provider robustness across Amp, Antigravity, Codex, AgentRouter, Command Code, and Pioneer
- Expand test coverage across providers, CLI, recommendations, usage tracking, and menubar behavior

### Fixed
- Fix menubar startup, refresh, disabled menu item, and Quit callback behavior
- Fix iTerm widget reliability and Python path detection
- Fix provider edge cases including auth fallbacks, header handling, cache behavior, and stale recommendations
- Remove unused imports and clean up lint/whitespace issues

## [1.1.0] - 2026-05-31

### Added
- Detect stale Codex limit data when the latest session is older than the limit window
- Mark stale Codex limits as likely reset and show refresh hints in text output
- Refresh stale Codex accounts automatically before showing status
- Add `auto_refresh` configuration option for Codex (defaults to true) to allow disabling automatic background refreshes
- Add `--sync-codex` to refresh all discovered Codex accounts before showing status

### Fixed
- Keep JSON output valid when refresh status messages would otherwise be printed
- Improve Antigravity CLI process tracing to properly discover descendant language server processes and extract dynamic ports
- Fix redundant configuration parsing in CommandCode, AgentRouter, and Pioneer providers

## [1.0.0] - 2026-05-28

### Added
- Unified CLI to check quotas across Codex, Amp, Antigravity, and OpenCode
- Smart recommendation engine (hard/quick/cli task tiers)
- Waste tracker with reset detection and historical reporting
- iTerm2 status bar widget for live quota display
- Multi-account Codex support (`~/.codex-*` discovery)
- Multi-profile Antigravity support (IDE + CLI profiles)
- Copilot CLI OTel spend tracking
- Privacy-first PII redaction (emails, paths)
- Configurable display settings (auto-hide, thresholds)
- `--watch` mode for live updates
- `--json` output for scripting
- Zero runtime dependencies (Python stdlib only)
