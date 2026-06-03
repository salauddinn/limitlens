# LimitLens Install & Uninstall Scripts

## Overview

Two bash scripts provide one-line installation and uninstallation for LimitLens:

- **`install.sh`** — Automated installer with zero manual steps
- **`uninstall.sh`** — Complete removal including LaunchAgent and optional config cleanup

---

## Install Script (`install.sh`)

### Usage
```bash
curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash
```

### What It Does
1. **OS Detection** — Automatically detects macOS vs Linux (exits on Windows)
2. **Python Check** — Ensures Python 3.9+ is installed
3. **pipx Installation** — Installs `pipx` via:
   - macOS: `brew install pipx`
   - Debian/Ubuntu: `sudo apt-get install pipx`
   - Fedora/RHEL: `sudo dnf install pipx`
   - Arch: `sudo pacman -Sy python-pipx`
   - Fallback: `python3 -m pip install --user pipx`
4. **LimitLens Installation**
   - macOS: `pipx install "git+....[mac]"` (includes `rumps` for menubar)
   - Linux: `pipx install "git+...."`
   - Handles upgrades if already installed
5. **macOS LaunchAgent (Optional)** — Prompts to register menubar app for auto-start at login

### Features
- **Idempotent** — Safe to run multiple times (upgrades if already installed)
- **Colored output** — Clear status indicators (✓ success, ⚠ warnings, ✗ errors)
- **Error handling** — Exits cleanly with helpful messages on failures
- **PATH auto-setup** — Runs `pipx ensurepath` to ensure binaries are accessible

---

## Uninstall Script (`uninstall.sh`)

### Usage
```bash
curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/uninstall.sh | bash
```

### What It Does
1. **Confirmation Prompt** — Lists everything that will be removed, requires explicit `y` to proceed
2. **menubar Process Cleanup** (macOS) — Kills running `limitlens-menubar` process
3. **LaunchAgent Removal** (macOS)
   - Unloads from `launchctl`
   - Deletes `~/Library/LaunchAgents/com.limitlens.menubar.plist`
   - Removes log file at `~/Library/Logs/limitlens-menubar.log`
4. **pipx Uninstall** — Removes the `limitlens` package
5. **Config Cleanup (Optional)** — Separate prompt for `~/.config/limitlens/` removal

### Features
- **Safe by default** — Requires explicit confirmation before touching files
- **Preserves user data** — Config directory gets its own prompt (not auto-deleted)
- **Cross-platform** — Works on both macOS and Linux
- **Helpful exit message** — Shows reinstall command at the end

---

## Testing

Both scripts have been tested in dry-run mode and are syntax-validated with `bash -n`.

### Manual Testing Checklist
- [x] Python version detection (requires 3.9+)
- [x] OS detection (macOS vs Linux)
- [x] pipx installation (multiple package managers)
- [x] LimitLens installation (fresh install)
- [x] LimitLens upgrade (when already installed)
- [x] LaunchAgent registration (macOS)
- [x] Uninstall confirmation flow
- [x] LaunchAgent cleanup (macOS)
- [x] Config preservation option
- [x] Syntax validation (`bash -n`)

### Test Commands
```bash
# Syntax check
bash -n install.sh
bash -n uninstall.sh

# Check Python version detection
python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'

# Check pipx presence
command -v pipx && pipx --version

# Check current install status
pipx list | grep limitlens
```

---

## Security Considerations

### For Users
- **Curl-pipe-bash risks** — These scripts execute with your user permissions. Review the script before running:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | less
  ```
- **sudo usage** — Scripts only use `sudo` for package manager operations (`apt`, `dnf`, `pacman`) when `pipx` is missing
- **No credential access** — Scripts never touch API keys, tokens, or credentials

### For Maintainers
- **Use `set -euo pipefail`** — Scripts exit immediately on errors
- **Validate inputs** — All user inputs are validated before execution
- **Clear error messages** — Every failure prints a helpful diagnostic
- **Idempotent operations** — Safe to run multiple times without side effects

---

## Troubleshooting

### "python3 not found"
Install Python 3.9+:
- macOS: `brew install python`
- Debian/Ubuntu: `sudo apt install python3`

### "pipx installation failed"
Install manually:
- macOS: `brew install pipx`
- Linux: See https://pipx.pypa.io/stable/installation/

### "limitlens command not found after install"
Run `pipx ensurepath` and open a new terminal session.

### "LaunchAgent not starting on macOS"
Check logs:
```bash
cat ~/Library/Logs/limitlens-menubar.log
```

Manually load:
```bash
launchctl load ~/Library/LaunchAgents/com.limitlens.menubar.plist
```

---

## Files Created/Modified

### Install Script
- `~/.local/bin/limitlens` (via pipx)
- `~/.local/bin/limitlens-menubar` (macOS only)
- `~/Library/LaunchAgents/com.limitlens.menubar.plist` (optional, macOS)
- Shell profile (via `pipx ensurepath` — adds PATH entry)

### Uninstall Script (Removes)
- Everything from install script
- `~/Library/Logs/limitlens-menubar.log` (macOS)
- `~/.config/limitlens/` (optional, with separate confirmation)

---

## Future Enhancements

- [ ] Windows support via PowerShell script
- [ ] Automatic update check and notification
- [ ] Optional systemd service for Linux (background monitoring)
- [ ] Homebrew tap for native `brew install limitlens`
- [ ] Progress bar for slow network connections
