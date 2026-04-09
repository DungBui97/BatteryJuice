#!/usr/bin/env bash
set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.batteryjuice.plist"
PLIST_LABEL="com.batteryjuice"
SUPPORT_DIR="$HOME/Library/Application Support/BatteryJuice"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}▶${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }

echo ""
echo "  🔋 BatteryJuice — Uninstaller"
echo "  ─────────────────────────────────"
echo ""

# Stop and remove launchd agent (use modern bootout; fall back to unload for older macOS)
if launchctl list "$PLIST_LABEL" &>/dev/null; then
    info "Stopping launchd agent..."
    GUI_DOMAIN="gui/$(id -u)"
    launchctl bootout "$GUI_DOMAIN/$PLIST_LABEL" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
    success "Agent stopped"
fi

if [ -f "$PLIST_DST" ]; then
    rm "$PLIST_DST"
    success "Plist removed"
fi

# Offer to remove data
echo ""
read -r -p "  Delete all battery data and reports? [y/N] " choice
if [[ "${choice}" =~ ^[Yy]$ ]]; then
    rm -rf "$SUPPORT_DIR"
    success "Data and reports deleted"
else
    warn "Data kept at: $SUPPORT_DIR"
fi

echo ""
success "BatteryJuice uninstalled."
echo ""
