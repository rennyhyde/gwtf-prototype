"""
serial_bridge.py — SwimOn ESP32 serial-to-UDP bridge

Reads >rpm: and >dir: lines from one or two ESP32s over USB serial and
converts them to the same UDP JSON packets that server.py expects.
Drop-in replacement for simulator.py — server.py and index.html need
no changes.

Run: python serial_bridge.py

To find the right serial port:
  Linux/Pi : ls /dev/ttyUSB* /dev/ttyACM*  (before and after plugging in)
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

# Serial port for each arm.  Set TANK_1_PORT = None if only one ESP32 is connected.
TANK_0_PORT = "/dev/ttyUSB0"   # left arm  — change to "COM3" etc. on Windows
TANK_1_PORT = None              # right arm — set when second ESP32 is ready

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

    current_rpm = 0.0
    current_dir = 0

    while True:
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=1.0)
            print(f"Tank {tank_id}: connected on {port} at {BAUD_RATE} baud")

            for raw in ser:
                line = raw.decode(errors="ignore").strip()

                if not line.startswith(">"):
                    # Debug lines from firmware (a_rise since_b=...) — skip
                    continue

                if line.startswith(">rpm:"):
                    try:
                        current_rpm = float(line[5:])
                    except ValueError:
                        pass

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

                    # Emit a UDP packet after each >dir: line.
                    # The firmware always sends >rpm: immediately before >dir:,
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
                    except OSError:
                        pass

        except serial.SerialException as e:
            print(f"Tank {tank_id}: serial error ({e}) — retrying in 2 s")
            time.sleep(2)
        except Exception as e:
            print(f"Tank {tank_id}: unexpected error ({e}) — retrying in 2 s")
            time.sleep(2)

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    configs = [(TANK_0_PORT, 0)]
    if TANK_1_PORT is not None:
        configs.append((TANK_1_PORT, 1))

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

    active_ports = [f"tank {tid} -> {p}" for p, tid in configs]
    print(f"Serial bridge: {', '.join(active_ports)}")
    print(f"  Forwarding to UDP {UDP_HOST}:{UDP_PORT}")
    print("Press Ctrl+C to stop.")

    try:
        threads[0].join()
    except KeyboardInterrupt:
        print("\nBridge stopped.")


if __name__ == "__main__":
    main()
