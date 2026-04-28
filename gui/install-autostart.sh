#!/usr/bin/env bash
# install-autostart.sh — installs SwimOn autostart entries for the Pi desktop
#
# Run once after cloning/pulling:
#   cd swimon-prototype/gui
#   bash install-autostart.sh
#
# To remove autostart:
#   rm ~/.config/autostart/swimon-*.desktop

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
START_SH="$SCRIPT_DIR/start.sh"
AUTOSTART_DIR="$HOME/.config/autostart"

# ── Pre-flight checks ──────────────────────────────────────────────────────────
if [ ! -f "$START_SH" ]; then
    echo "ERROR: start.sh not found at $START_SH"
    exit 1
fi

chmod +x "$START_SH"
echo "start.sh is executable: $START_SH"

mkdir -p "$AUTOSTART_DIR"

# ── swimon-server.desktop ──────────────────────────────────────────────────────
cat > "$AUTOSTART_DIR/swimon-server.desktop" << EOF
[Desktop Entry]
Type=Application
Name=SwimOn Server
Exec=bash $START_SH
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
echo "Created: $AUTOSTART_DIR/swimon-server.desktop"

# ── swimon-kiosk.desktop ───────────────────────────────────────────────────────
# Delay gives server.py time to start before Chromium tries to connect.
# Tries chromium-browser first (Pi OS), falls back to chromium (other distros).
CHROMIUM_BIN=""
if command -v chromium-browser &>/dev/null; then
    CHROMIUM_BIN="chromium-browser"
elif command -v chromium &>/dev/null; then
    CHROMIUM_BIN="chromium"
else
    echo "WARNING: chromium not found — skipping kiosk desktop entry"
    echo "  Install with: sudo apt install chromium-browser"
    CHROMIUM_BIN=""
fi

if [ -n "$CHROMIUM_BIN" ]; then
    cat > "$AUTOSTART_DIR/swimon-kiosk.desktop" << EOF
[Desktop Entry]
Type=Application
Name=SwimOn Kiosk
Exec=bash -c "sleep 6 && $CHROMIUM_BIN --kiosk --app=http://localhost:5000 --disable-infobars --noerrdialogs --check-for-update-interval=31536000"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
    echo "Created: $AUTOSTART_DIR/swimon-kiosk.desktop"
fi

# ── Screensaver off ────────────────────────────────────────────────────────────
cat > "$AUTOSTART_DIR/swimon-nodpms.desktop" << EOF
[Desktop Entry]
Type=Application
Name=SwimOn Disable DPMS
Exec=bash -c "xset s off && xset -dpms && xset s noblank"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
echo "Created: $AUTOSTART_DIR/swimon-nodpms.desktop"

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "Done. Autostart entries installed."
echo ""
echo "To test without rebooting:"
echo "  bash $START_SH"
echo ""
echo "To verify the .desktop files:"
echo "  cat $AUTOSTART_DIR/swimon-server.desktop"
echo "  cat $AUTOSTART_DIR/swimon-kiosk.desktop"
