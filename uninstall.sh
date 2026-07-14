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
#   • config directory (with user confirmation)
# ────────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CONFIG_DIR="$HOME/.config/limitlens"

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
  echo "  • iTerm2 widget (if installed)"
fi
echo "  • config directory ($CONFIG_DIR)  [only if you confirm]"
echo ""
read -r -p "$(echo -e "${BOLD}Continue with uninstall? [y/N]:${RESET} ")" CONFIRM
CONFIRM="${CONFIRM:-N}"
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── Remove iTerm2 widget (macOS only) ──────────────────────────────────────────
if $IS_MAC; then
  WIDGET_FILE="$HOME/Library/Application Support/iTerm2/Scripts/limitlens_widget.py"
  if [[ -f "$WIDGET_FILE" ]]; then
    rm -f "$WIDGET_FILE"
    success "iTerm2 widget removed."
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

# ── Optionally remove cache folder ──────────────────────────────────────────────
CACHE_DIR="$HOME/.cache/limitlens"
if [[ -d "$CACHE_DIR" ]]; then
  echo ""
  read -r -p "$(echo -e "${BOLD}Remove cache folder ($CACHE_DIR)? [y/N]:${RESET} ")" REMOVE_CACHE
  REMOVE_CACHE="${REMOVE_CACHE:-N}"
  if [[ "$REMOVE_CACHE" =~ ^[Yy]$ ]]; then
    rm -rf "$CACHE_DIR"
    success "Cache folder removed."
  else
    warn "Cache kept at $CACHE_DIR"
  fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}LimitLens has been uninstalled.${RESET}"
echo ""
echo "  To reinstall at any time:"
echo -e "  ${CYAN}curl -fsSL https://raw.githubusercontent.com/salauddinn/limitlens/main/install.sh | bash${RESET}"
echo ""
