#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────────
# LimitLens Uninstaller
# ────────────────────────────────────────────────────────────────────────────────
# Completely removes LimitLens and all associated files.
# 
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/uninstall.sh | bash
#
# What it removes:
#   • pipx package (limitlens)
#   • menubar LaunchAgent and logs (macOS only)
#   • config directory (with user confirmation)
# ────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LAUNCHAGENT_LABEL="com.limitlens.menubar"
LAUNCHAGENT_PLIST="$HOME/Library/LaunchAgents/${LAUNCHAGENT_LABEL}.plist"
CONFIG_DIR="$HOME/.config/limitlens"
LOG_FILE="$HOME/Library/Logs/limitlens-menubar.log"

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
case "$OSTYPE" in
  darwin*) IS_MAC=true ;;
  linux*)  ;;
  *)       die "Unsupported OS: $OSTYPE." ;;
esac

# ── Confirm ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}This will remove LimitLens and all associated files.${RESET}"
echo ""
echo "  • pipx package (limitlens)"
if $IS_MAC; then
  echo "  • menubar LaunchAgent (auto-start at login)"
  echo "  • menubar log ($LOG_FILE)"
fi
echo "  • config directory ($CONFIG_DIR)  [only if you confirm]"
echo ""
read -r -p "$(echo -e "${BOLD}Continue with uninstall? [y/N]:${RESET} ")" CONFIRM
CONFIRM="${CONFIRM:-N}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Stop & remove LaunchAgent (macOS only) ─────────────────────────────────────
if $IS_MAC; then
  info "Removing menubar autostart..."

  # Kill running menubar process if any
  if pgrep -f "limitlens-menubar" &>/dev/null; then
    pkill -f "limitlens-menubar" 2>/dev/null || true
    success "Stopped running menubar process."
  fi

  # Unload and delete LaunchAgent plist
  if [[ -f "$LAUNCHAGENT_PLIST" ]]; then
    launchctl unload "$LAUNCHAGENT_PLIST" 2>/dev/null || true
    rm -f "$LAUNCHAGENT_PLIST"
    success "LaunchAgent removed."
  else
    warn "No LaunchAgent found — skipping."
  fi

  # Remove log file
  if [[ -f "$LOG_FILE" ]]; then
    rm -f "$LOG_FILE"
    success "Log file removed."
  fi
fi

# ── Uninstall via pipx ─────────────────────────────────────────────────────────
info "Uninstalling LimitLens via pipx..."

export PATH="$PATH:$HOME/.local/bin"

if command -v pipx &>/dev/null && pipx list 2>/dev/null | grep -q "limitlens"; then
  pipx uninstall limitlens
  success "LimitLens uninstalled."
else
  warn "LimitLens not found in pipx — may have been installed another way."
  warn "If you installed manually, remove it with: pip uninstall limitlens"
fi

# ── Optionally remove config ───────────────────────────────────────────────────
if [[ -d "$CONFIG_DIR" ]]; then
  echo ""
  read -r -p "$(echo -e "${BOLD}Remove config directory ($CONFIG_DIR)? [y/N]:${RESET} ")" REMOVE_CONFIG
  REMOVE_CONFIG="${REMOVE_CONFIG:-N}"
  if [[ "$REMOVE_CONFIG" =~ ^[Yy]$ ]]; then
    rm -rf "$CONFIG_DIR"
    success "Config directory removed."
  else
    warn "Config kept at $CONFIG_DIR"
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}LimitLens has been uninstalled.${RESET}"
echo ""
echo "  To reinstall at any time:"
echo -e "  ${CYAN}curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash${RESET}"
echo ""
