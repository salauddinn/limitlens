#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────────
# LimitLens Installer
# ────────────────────────────────────────────────────────────────────────────────
# Automatically installs LimitLens and all dependencies on macOS & Linux.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash
#
# What it does:
#   • Detects your OS (macOS or Linux)
#   • Ensures Python 3.9+ is installed
#   • Installs pipx if missing (via brew/apt/dnf/pacman/pip)
#   • Installs LimitLens (with [mac] extras on macOS)
#   • Optionally registers menubar app to start at login (macOS only)
# ────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO="https://github.com/salauddinn/limitlens.git"
LAUNCHAGENT_LABEL="com.limitlens.menubar"
LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCHAGENT_LABEL}.plist"

# ── Colours ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; YELLOW=''; GREEN=''; CYAN=''; BOLD=''; RESET=''
fi

info()    { echo -e "${CYAN}${BOLD}==> ${RESET}${BOLD}$*${RESET}"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
die()     { echo -e "${RED}✗ Error:${RESET} $*" >&2; exit 1; }

# ── OS Detection ───────────────────────────────────────────────────────────────
IS_MAC=false
IS_LINUX=false
case "$OSTYPE" in
  darwin*)  IS_MAC=true ;;
  linux*)   IS_LINUX=true ;;
  *)        die "Unsupported OS: $OSTYPE. Only macOS and Linux are supported." ;;
esac

# ── Python 3 check ─────────────────────────────────────────────────────────────
info "Checking for Python 3..."
if ! command -v python3 &>/dev/null; then
  die "python3 not found. Please install Python 3.9+ first.\n  macOS: brew install python\n  Linux: sudo apt install python3"
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 9) ]]; then
  die "Python 3.9+ is required (found $PY_VERSION). Please upgrade."
fi
success "Python $PY_VERSION found."

# ── pipx check / install ────────────────────────────────────────────────────────
info "Checking for pipx..."
if ! command -v pipx &>/dev/null; then
  warn "pipx not found. Attempting to install..."

  if $IS_MAC && command -v brew &>/dev/null; then
    brew install pipx
    pipx ensurepath
  elif $IS_LINUX && command -v apt-get &>/dev/null; then
    sudo apt-get install -y pipx
    pipx ensurepath
  elif $IS_LINUX && command -v dnf &>/dev/null; then
    sudo dnf install -y pipx
    pipx ensurepath
  elif $IS_LINUX && command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm python-pipx
    pipx ensurepath
  else
    # Fallback: install via pip into user space
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
  fi

  # Re-source PATH in case pipx was just added
  export PATH="$PATH:$HOME/.local/bin"

  command -v pipx &>/dev/null || die "pipx installation failed. Please install it manually: https://pipx.pypa.io"
fi
success "pipx found."

# ── Install LimitLens ──────────────────────────────────────────────────────────
info "Installing LimitLens..."

# Reinstall cleanly if already present
if pipx list 2>/dev/null | grep -q "limitlens"; then
  warn "LimitLens is already installed. Upgrading..."
  if $IS_MAC; then
    pipx upgrade limitlens || pipx reinstall limitlens
  else
    pipx upgrade limitlens || pipx reinstall limitlens
  fi
else
  if $IS_MAC; then
    pipx install "git+${REPO}[mac]"
  else
    pipx install "git+${REPO}"
  fi
fi

success "LimitLens installed."

# ── Verify binary is reachable ─────────────────────────────────────────────────
export PATH="$PATH:$HOME/.local/bin"
if ! command -v limitlens &>/dev/null; then
  warn "'limitlens' command not found on PATH. You may need to run:"
  echo "    pipx ensurepath"
  echo "  then open a new terminal session."
fi

# ── macOS: offer autostart via LaunchAgent ─────────────────────────────────────
if $IS_MAC && command -v limitlens-menubar &>/dev/null; then
  echo ""
  read -r -p "$(echo -e "${BOLD}Start LimitLens menubar app automatically at login? [Y/n]:${RESET} ")" AUTOSTART
  AUTOSTART="${AUTOSTART:-Y}"

  if [[ "$AUTOSTART" =~ ^[Yy]$ ]]; then
    MENUBAR_BIN="$(command -v limitlens-menubar)"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$LAUNCHAGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LAUNCHAGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${MENUBAR_BIN}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/limitlens-menubar.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/limitlens-menubar.log</string>
</dict>
</plist>
PLIST
    launchctl load "$LAUNCHAGENT_PLIST" 2>/dev/null || true
    success "LaunchAgent registered — menubar app will start at next login."
    info "Starting menubar app now..."
    nohup "$MENUBAR_BIN" &>/dev/null &
    success "Menubar app launched."
  fi
fi

# ── macOS: auto-install iTerm2 widget ──────────────────────────────────────────
if $IS_MAC && [[ -d "$HOME/Library/Application Support/iTerm2/Scripts" ]]; then
  ITERM_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
  # Prefer the version-matched widget shipped inside the installed package.
  if command -v limitlens-iterm-widget &>/dev/null; then
    if limitlens-iterm-widget --install; then
      success "iTerm2 widget auto-installed to Scripts menu."
    fi
  else
    # Fallback: fetch from the default branch (version may not match).
    WIDGET_URL="https://raw.githubusercontent.com/salauddinn/limitlens/main/limitlens/iterm_widget.py"
    info "Downloading iTerm2 widget from GitHub to target directory..."
    DOWNLOADED=false
    if command -v curl &>/dev/null; then
      curl -fsSL "$WIDGET_URL" -o "$ITERM_DIR/limitlens_widget.py" && DOWNLOADED=true
    elif command -v wget &>/dev/null; then
      wget -q "$WIDGET_URL" -O "$ITERM_DIR/limitlens_widget.py" && DOWNLOADED=true
    fi
    if $DOWNLOADED; then
      chmod +x "$ITERM_DIR/limitlens_widget.py"
      success "iTerm2 widget auto-installed to Scripts menu."
    fi
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}LimitLens is ready!${RESET}"
echo ""
echo "  limitlens              # full quota dashboard"
echo "  limitlens --reco       # smart tool recommendation"
echo "  limitlens --watch      # live refresh every 5s"
echo "  limitlens --waste      # 7-day waste report"
if $IS_MAC; then
  echo "  limitlens-menubar      # menubar tray app"
fi
echo ""
echo -e "  Docs: ${CYAN}https://github.com/salauddinn/limitlens${RESET}"
