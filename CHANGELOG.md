# Changelog

All notable changes to this project will be documented in this file.

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
