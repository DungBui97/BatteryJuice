#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# BatteryJuice — Install Script
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/BatteryJuice"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_SRC="$REPO_DIR/com.batteryjuice.plist"
PLIST_DST="$LAUNCH_AGENTS/com.batteryjuice.plist"
PLIST_LABEL="com.batteryjuice"
APP_PY="$REPO_DIR/src/app.py"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}▶${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
die()     { echo -e "${RED}✗${NC} $*"; exit 1; }

echo ""
echo "  🔋 BatteryJuice — Installer"
echo "  ─────────────────────────────────"
echo ""

# ── 1. Check Python ───────────────────────────────────────────────────────────
info "Checking Python 3..."
PYTHON3=$(command -v python3 || true)
[ -z "$PYTHON3" ] && die "Python 3 not found. Install from https://python.org or via Homebrew: brew install python"
PY_VER=$("$PYTHON3" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 8 ] && die "Python 3.8+ required (found $PY_VER)"
success "Python $PY_VER found at $PYTHON3"

# ── 2. Install Python dependencies ────────────────────────────────────────────
info "Installing Python dependencies..."
if "$PYTHON3" -m pip install --quiet --user -r "$REPO_DIR/requirements.txt" 2>/tmp/bj_pip_err; then
    success "Dependencies installed"
else
    # Retry with --break-system-packages for managed environments (Homebrew Python 3.12+)
    if "$PYTHON3" -m pip install --quiet --user --break-system-packages -r "$REPO_DIR/requirements.txt" 2>/tmp/bj_pip_err2; then
        success "Dependencies installed (managed environment)"
    else
        cat /tmp/bj_pip_err2
        die "Failed to install Python dependencies. See error above."
    fi
fi

# ── 3. Create support directories ────────────────────────────────────────────
info "Creating support directories..."
mkdir -p "$SUPPORT_DIR/reports"
mkdir -p "$LAUNCH_AGENTS"

# Copy default config if not present
if [ ! -f "$SUPPORT_DIR/config.json" ]; then
    cp "$REPO_DIR/config.json" "$SUPPORT_DIR/config.json"
    success "Default config copied to $SUPPORT_DIR/config.json"
else
    warn "config.json already exists — skipping (edit manually at $SUPPORT_DIR/config.json)"
fi

# ── 4. Install launchd plist ──────────────────────────────────────────────────
info "Installing launchd agent..."

# Unload existing if running (use modern bootout; fall back to unload for older macOS)
GUI_DOMAIN="gui/$(id -u)"
if launchctl list "$PLIST_LABEL" &>/dev/null; then
    warn "Existing agent found — unloading first..."
    launchctl bootout "$GUI_DOMAIN/$PLIST_LABEL" 2>/dev/null \
        || launchctl unload "$PLIST_DST" 2>/dev/null \
        || true
fi

# Substitute real paths into plist
sed \
    -e "s|__PYTHON3__|$PYTHON3|g" \
    -e "s|__APP_PY__|$APP_PY|g" \
    -e "s|__SUPPORT_DIR__|$SUPPORT_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Use modern bootstrap; fall back to load for older macOS
launchctl bootstrap "$GUI_DOMAIN" "$PLIST_DST" 2>/dev/null \
    || launchctl load "$PLIST_DST"
success "launchd agent installed and started"

# ── 5. First snapshot ─────────────────────────────────────────────────────────
info "Running first battery snapshot..."
"$PYTHON3" - "$REPO_DIR" <<'EOF'
import sys, pathlib, json, os
repo_dir = sys.argv[1]
sys.path.insert(0, str(pathlib.Path(repo_dir) / "src"))
support = pathlib.Path.home() / "Library" / "Application Support" / "BatteryJuice"
cfg_path = support / "config.json"
cfg = json.loads(cfg_path.read_text())
from collector import collect
from database import init_db, insert_snapshot
db = os.path.expanduser(cfg["db_path"])
init_db(db)
data = collect()
if data:
    insert_snapshot(db, data)
    print(f"  Snapshot saved: {data.get('current_pct')}% charge, {data.get('cycle_count')} cycles")
EOF
success "First snapshot collected"

echo ""
echo "  ─────────────────────────────────"
success "BatteryJuice is now running in your menu bar! 🔋"
echo ""
echo "  To uninstall: ./uninstall.sh"
echo "  Config:       $SUPPORT_DIR/config.json"
echo "  Reports:      $SUPPORT_DIR/reports/"
echo ""
