# LimitLens Operations Runbook

## 1. Overview
This runbook provides operational instructions for deploying, troubleshooting, and maintaining LimitLens in a production environment. Since LimitLens is primarily a zero-dependency CLI tool installed locally by developers, "production" refers to its stable execution across diverse developer machines (macOS, Linux) without causing system instability or leaking credentials.

## 2. Deployment Readiness & Environment Setup

LimitLens is designed for local installation.

### Installation Strategy
The recommended installation method uses `pipx` to isolate dependencies and prevent system Python environment conflicts:
```bash
curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash
```

### Configuration
On first run, LimitLens auto-detects installed providers and generates a configuration file at `~/.config/limitlens/config.json`.
- **Overrides:** Environment variables can be used to override configuration dynamically (e.g., `LIMITLENS_DISPLAY_AUTO_HIDE_ENABLED=true`).
- **Data Location:** All state and data are stored in `~/.config/limitlens/` or cached locally. No remote tracking occurs.

## 3. Monitoring & Logging

LimitLens is a stateless CLI. It does not run a persistent background daemon, except for the optional macOS menubar app (`limitlens-menubar`).

### Logging for Menubar App
The menubar app writes logs to `~/Library/Logs/limitlens-menubar.log`.
To monitor logs:
```bash
tail -f ~/Library/Logs/limitlens-menubar.log
```

## 4. Troubleshooting Common Issues

### Issue: Provider Quota Not Showing
- **Cause:** The provider might be disabled in `~/.config/limitlens/config.json`, or the CLI couldn't find the associated credentials (e.g., `~/.codex-*` directory missing).
- **Fix:** Check `config.json` and ensure the provider is enabled. Run `limitlens --verbose` for detailed error messages.

### Issue: Menubar App Fails to Start
- **Cause:** Python dependencies (`rumps`) might be missing, or `LaunchAgent` is misconfigured.
- **Fix:** Ensure LimitLens was installed with macOS extras: `pipx install "git+https://github.com/salauddinn/limitlens.git[mac]"`. Check the log file for stack traces.

### Issue: "limitlens: command not found"
- **Cause:** The `pipx` binary directory is not in the system PATH.
- **Fix:** Run `pipx ensurepath` and restart the terminal.

## 5. Security & Operational Risks

- **Credential Leakage:** LimitLens relies on local OS APIs and file parsing. It redacts emails and system paths using the `redact_text` and `redact_email` utilities in `limitlens/core.py`.
- **Subprocesses:** Secure command execution uses explicit lists (`shell=False`) in `limitlens/keychain.py` to prevent shell injection.
- **Vulnerability Scans:** Periodic checks with `safety` and `bandit` ensure third-party and codebase security. Always use the pinned constraints in `pyproject.toml`.

## 6. Build and Release Process
- **Build:** `python -m build` or use `setuptools.build_meta` via `pyproject.toml`.
- **Validation:** Run all checks before pushing:
  ```bash
  source .venv/bin/activate
  pytest
  ruff check .
  bandit -r limitlens
  ```
