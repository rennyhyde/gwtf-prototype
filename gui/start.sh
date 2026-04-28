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

# Always run relative to this script's own directory
cd "$(dirname "$0")"

# Kill any leftover processes from a previous run
pkill -f "python.*server.py" 2>/dev/null
pkill -f "python.*serial_bridge.py" 2>/dev/null
sleep 0.5

echo "Starting SwimOn server..."
python3 server.py &
SERVER_PID=$!
echo "  server.py running (PID $SERVER_PID) -> http://localhost:5000"

# Give the server a moment to bind its ports before the bridge starts sending
sleep 1

# Kill the server when this script exits (Ctrl+C, error, or normal exit)
trap "echo; echo 'Stopping...'; kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null" EXIT INT TERM

echo "Starting serial bridge..."
echo "  (Ctrl+C to stop both)"
echo ""
python3 serial_bridge.py
