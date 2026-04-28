"""
serial_bridge.py — SwimOn ESP32 serial-to-UDP bridge

Reads >rpm: and >dir: lines from one or two ESP32s over USB serial and
converts them to the same UDP JSON packets that server.py expects.
Drop-in replacement for simulator.py — server.py and index.html need
no changes.

Run: python serial_bridge.py

To find the right serial port:
  Linux/Pi : ls /dev/ttyUSB* /dev/ttyACM*  (before and after plugging in)
             ls -la /dev/serial/by-path/    (stable names tied to physical port)
  Windows  : Device Manager -> Ports (COM & LPT)

The ESP32 firmware (rs-pulley-detect) outputs at 10 Hz:
  >rpm:X.XX       actual RPM as a float
  >dir:CW         pull phase  -> dir = +1
  >dir:CCW        recovery    -> dir = -1
  >dir:UNK        direction not yet confirmed (4-step hysteresis settling)
  >dir:STOP       stalled (no pulse in 3 s)
  a_rise ...      debug lines (DEBUG_STEPS=true in firmware) — ignored here

When the second ESP32 is ready, set TANK_1_PORT to its serial port.
"""

import os
import serial
import socket
import json
import time
import threading
import math

# ── Configuration ─────────────────────────────────────────────────────────────
UDP_HOST    = "127.0.0.1"
UDP_PORT    = 9000
BAUD_RATE   = 115200

# Serial port for each arm.  Use absolute paths.
# Run: ls -la /dev/serial/by-path/   to find your device names.
# Set TANK_1_PORT = None if only one ESP32 is connected.
TANK_0_PORT = "/dev/serial/by-path/platform-xhci-hcd.0-usb-0:2:1.0"   # left arm
TANK_1_PORT = None # "/dev/serial/by-path/platform-xhci-hcd.1-usb-0:2:1.0"

# How often to print a status line (in UDP packets sent, ~10/s per arm)
STATUS_EVERY = 30   # print roughly every 3 seconds

# Force model (must match simulator.py so charts are on the same scale).
# F = C_EFF * omega^2  where omega = RPM * 2*pi/60
PULLEY_RADIUS_M = 0.1016        # 8-inch diameter / 2
PEAK_FORCE_N    = 42.0          # N at PEAK_RPM (recreational swimmer target)
PEAK_RPM        = 115.0         # RPM corresponding to peak force
_omega_peak     = PEAK_RPM * 2.0 * math.pi / 60.0
C_EFF           = PEAK_FORCE_N / (_omega_peak ** 2)


# ── Force / velocity from RPM ─────────────────────────────────────────────────

def rpm_to_force_vel(rpm: float, direction: int) -> tuple[float, float]:
    """
    Converts RPM + direction to force (N) and rope velocity (m/s).
    Force is zero during recovery (direction == -1).
    Velocity is negative during recovery (rope retracting).
    """
    omega   = rpm * 2.0 * math.pi / 60.0
    force_n = C_EFF * omega ** 2 if direction == 1 else 0.0
    vel_ms  = omega * PULLEY_RADIUS_M * direction if direction != 0 else 0.0
    return round(force_n, 2), round(vel_ms, 3)


# ── Per-tank serial reader ────────────────────────────────────────────────────

def bridge_loop(port: str, tank_id: int, sock: socket.socket) -> None:
    """
    Reads one ESP32's serial stream indefinitely and sends UDP packets.
    Reconnects automatically if the serial port drops (e.g. cable pull).
    """
    addr    = (UDP_HOST, UDP_PORT)
    t_start = time.monotonic()
    label   = f"[tank {tank_id}]"

    # Resolve the port to an absolute path so relative paths don't silently fail
    abs_port = os.path.realpath(port)
    if abs_port != port:
        print(f"{label} Port resolved: {port} -> {abs_port}")

    current_rpm  = 0.0
    current_dir  = 0
    prev_dir     = 0
    pkt_count    = 0
    line_count   = 0

    while True:
        try:
            # Check the port exists before trying to open it
            if not os.path.exists(abs_port):
                print(f"{label} Port not found: {abs_port}")
                print(f"{label}   Run: ls -la /dev/serial/by-path/  or  ls /dev/ttyUSB* /dev/ttyACM*")
                time.sleep(3)
                continue

            ser = serial.Serial(abs_port, BAUD_RATE, timeout=1.0)
            print(f"{label} Connected on {abs_port} at {BAUD_RATE} baud")
            print(f"{label} Waiting for data... (firmware should send >rpm: and >dir: lines)")

            for raw in ser:
                line = raw.decode(errors="ignore").strip()
                line_count += 1

                # Print every raw line for the first 10, then go quiet on debug lines
                if line_count <= 10:
                    print(f"{label} raw[{line_count:02d}]: {repr(line)}")

                if not line.startswith(">"):
                    # Debug lines from firmware (a_rise since_b=...) — skip
                    continue

                if line.startswith(">rpm:"):
                    try:
                        current_rpm = float(line[5:])
                    except ValueError:
                        print(f"{label} WARNING: could not parse rpm from: {repr(line)}")

                elif line.startswith(">dir:"):
                    token = line[5:]
                    if token == "CW":
                        current_dir = 1
                    elif token == "CCW":
                        current_dir = -1
                    else:
                        # UNK (direction settling) or STOP (stalled)
                        current_dir = 0
                        if token == "STOP":
                            current_rpm = 0.0

                    # Print direction transitions — these are stroke events
                    if current_dir != prev_dir:
                        dir_name = {1: "PULL (CW)", -1: "RECOVERY (CCW)", 0: f"STOPPED ({token})"}.get(current_dir, token)
                        print(f"{label} Direction change -> {dir_name}  rpm={current_rpm:.1f}")
                        prev_dir = current_dir

                    # Send UDP packet after each >dir: line.
                    # Firmware always sends >rpm: immediately before >dir:,
                    # so current_rpm is already up to date.
                    force_n, vel_ms = rpm_to_force_vel(current_rpm, current_dir)
                    pkt = json.dumps({
                        "t":       round(time.monotonic() - t_start, 3),
                        "tank":    tank_id,
                        "rpm":     round(current_rpm, 2),
                        "dir":     current_dir,
                        "force_n": force_n,
                        "vel_ms":  vel_ms,
                    }).encode()

                    try:
                        sock.sendto(pkt, addr)
                        pkt_count += 1
                    except OSError as e:
                        print(f"{label} UDP send error: {e}")

                    # Periodic status line
                    if pkt_count % STATUS_EVERY == 0:
                        elapsed = time.monotonic() - t_start
                        dir_str = {1: "pull", -1: "recv", 0: "stop"}.get(current_dir, "?")
                        print(f"{label} t={elapsed:.0f}s  pkts={pkt_count}  "
                              f"rpm={current_rpm:6.1f}  dir={dir_str}  "
                              f"force={force_n:.1f}N  vel={vel_ms:.3f}m/s")

        except serial.SerialException as e:
            print(f"{label} Serial error: {e} — retrying in 2 s")
            time.sleep(2)
        except Exception as e:
            print(f"{label} Unexpected error: {e} — retrying in 2 s")
            time.sleep(2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    configs = [(TANK_0_PORT, 0)]
    if TANK_1_PORT is not None:
        configs.append((TANK_1_PORT, 1))

    print("SwimOn serial bridge")
    print(f"  UDP target: {UDP_HOST}:{UDP_PORT}")
    for port, tank_id in configs:
        abs_port = os.path.realpath(port)
        exists   = "OK" if os.path.exists(abs_port) else "NOT FOUND"
        print(f"  tank {tank_id}: {abs_port}  [{exists}]")
    print()

    threads = []
    for port, tank_id in configs:
        t = threading.Thread(
            target=bridge_loop,
            args=(port, tank_id, sock),
            daemon=True,
            name=f"bridge-tank{tank_id}",
        )
        t.start()
        threads.append(t)

    print("Press Ctrl+C to stop.\n")
    try:
        threads[0].join()
    except KeyboardInterrupt:
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()
