#!/usr/bin/env bash
# start.sh — starts the SwimOn server and serial bridge
#
# Usage:
#   cd swimon-prototype/gui
#   chmod +x start.sh     (first time only)
#   ./start.sh
#
# Or from anywhere:
#   bash /path/to/swimon-prototype/gui/start.sh
#
# Ctrl+C stops both processes cleanly.
# Logs are always written to start.sh's directory/swimon.log
# so you can check them even when run headlessly via autostart.

# Always run relative to this script's own directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOG="$SCRIPT_DIR/swimon.log"

# Redirect all output (stdout + stderr) to log file AND terminal if interactive
if [ -t 1 ]; then
    # Running in a terminal — show output live and also save to log
    exec > >(tee "$LOG") 2>&1
else
    # Running headlessly (autostart) — save to log only
    exec >> "$LOG" 2>&1
fi

echo "========================================"
echo "SwimOn starting: $(date)"
echo "Working dir: $SCRIPT_DIR"
echo "Python: $(which python3) $(python3 --version 2>&1)"
echo "========================================"

# Kill any leftover processes from a previous run
pkill -f "python.*server.py" 2>/dev/null
pkill -f "python.*serial_bridge.py" 2>/dev/null
sleep 0.5

echo "Starting server.py..."
python3 server.py &
SERVER_PID=$!
echo "  server.py PID=$SERVER_PID"

# Wait up to 5 seconds for the server to bind port 5000
for i in $(seq 1 10); do
    sleep 0.5
    if ss -tlnp 2>/dev/null | grep -q ':5000'; then
        echo "  server.py is listening on port 5000 (after ${i} x 0.5s)"
        break
    fi
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo "ERROR: server.py exited unexpectedly — check log above"
        exit 1
    fi
done

if ! ss -tlnp 2>/dev/null | grep -q ':5000'; then
    echo "WARNING: port 5000 not detected after 5s — server may still be starting"
fi

# Kill the server when this script exits (Ctrl+C, error, or normal exit)
trap "echo; echo 'Stopping: $(date)'; kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null" EXIT INT TERM

echo "Starting serial_bridge.py..."
echo "  (Ctrl+C or kill $$ to stop both)"
echo ""
python3 serial_bridge.py
