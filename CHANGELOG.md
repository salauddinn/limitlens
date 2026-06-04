# Changelog

All notable changes to this project will be documented in this file.

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
