"""
simulator.py — SwimOn dummy data generator

Simulates two-arm freestyle swimming strokes and sends UDP JSON packets
to server.py at SEND_RATE_HZ per arm. Replace this with real ESP32 UDP
output when firmware is ready — the packet schema is identical.

Run: python simulator.py
"""

import socket
import json
import time
import math
import threading
import numpy as np

# ── Configuration ─────────────────────────────────────────────────────────────
UDP_HOST        = "127.0.0.1"
UDP_PORT        = 9000
SEND_RATE_HZ    = 10            # packets per second per tank

PULLEY_RADIUS_M = 0.1016        # 8-inch diameter / 2 = 4 inches = 0.1016 m

TARGET_SPM      = 45            # strokes per minute, per arm (recreational)
PULL_FRACTION   = 0.54          # pull phase = 54% of stroke period (~0.72 s at 45 SPM)
PEAK_AT         = 0.45          # bell-curve peak at 45% through pull phase (biomechanical)

PEAK_FORCE_N    = 42.0          # peak force at pull peak (N) — recreational range 35–50 N
PEAK_RPM        = 115.0         # RPM at peak force (≈1.2 m/s rope speed)

NOISE_FORCE_STD = 0.8           # N — sensor noise added to force
NOISE_RPM_STD   = 2.0           # RPM — sensor noise added to RPM

# Derived: C_eff such that force_n = C_eff * omega^2
# (combines C_flywheel and 1/r into one constant for the force model)
_omega_peak = PEAK_RPM * 2.0 * math.pi / 60.0
C_EFF = PEAK_FORCE_N / (_omega_peak ** 2)

# ── Stroke geometry ────────────────────────────────────────────────────────────
STROKE_PERIOD_S = 60.0 / TARGET_SPM          # 1.333 s at 45 SPM
PHASE_OFFSET_S  = STROKE_PERIOD_S / 2.0      # right arm offset by half period


def _bell(phi_pull: float) -> float:
    """
    Asymmetric bell curve over pull phase [0, 1] → [0, 1].
    Peak at PEAK_AT (45%). Biomechanically accurate: fast rise, slower decay.
    """
    if phi_pull <= 0.0:
        return 0.0
    if phi_pull >= 1.0:
        return 0.0
    if phi_pull < PEAK_AT:
        return 0.5 * (1.0 - math.cos(math.pi * phi_pull / PEAK_AT))
    else:
        return 0.5 * (1.0 + math.cos(math.pi * (phi_pull - PEAK_AT) / (1.0 - PEAK_AT)))


def _compute_state(phi: float) -> tuple[float, int, float, float]:
    """
    Given normalized phase within stroke period [0, 1], return
    (rpm, direction, force_n, vel_ms).
    """
    if phi < PULL_FRACTION:
        # Pull phase
        phi_pull = phi / PULL_FRACTION
        norm = _bell(phi_pull)

        force_n = max(0.0, norm * PEAK_FORCE_N + np.random.normal(0.0, NOISE_FORCE_STD))
        omega = math.sqrt(max(0.0, force_n) / C_EFF) if force_n > 0.5 else 0.0
        rpm = omega * 60.0 / (2.0 * math.pi)
        rpm = max(0.0, rpm + np.random.normal(0.0, NOISE_RPM_STD))
        vel_ms = omega * PULLEY_RADIUS_M
        return rpm, 1, force_n, vel_ms

    else:
        # Recovery phase — spring returns rope, user relaxes
        phi_rec = (phi - PULL_FRACTION) / (1.0 - PULL_FRACTION)
        rpm_recover = PEAK_RPM * 0.15 * math.exp(-5.0 * phi_rec)
        rpm_recover = max(0.0, rpm_recover + np.random.normal(0.0, NOISE_RPM_STD * 0.3))
        omega = rpm_recover * 2.0 * math.pi / 60.0
        vel_ms = -omega * PULLEY_RADIUS_M  # negative = rope retracting
        return rpm_recover, -1, 0.0, vel_ms


def tank_loop(tank_id: int, phase_offset: float, sock: socket.socket) -> None:
    """
    Runs in its own thread. Sends UDP packets at SEND_RATE_HZ for one tank.
    tank_id    : 0 = left arm, 1 = right arm
    phase_offset: seconds to shift the stroke phase for this arm
    """
    dt = 1.0 / SEND_RATE_HZ
    addr = (UDP_HOST, UDP_PORT)
    t_start = time.monotonic()

    while True:
        loop_start = time.monotonic()
        t_abs = loop_start - t_start

        # Phase of this arm within its stroke cycle
        t_shifted = (t_abs - phase_offset) % STROKE_PERIOD_S
        phi = t_shifted / STROKE_PERIOD_S

        rpm, direction, force_n, vel_ms = _compute_state(phi)

        packet = json.dumps({
            "t":       round(t_abs, 3),
            "tank":    tank_id,
            "rpm":     round(rpm, 2),
            "dir":     direction,
            "force_n": round(force_n, 2),
            "vel_ms":  round(vel_ms, 3),
        }).encode()

        try:
            sock.sendto(packet, addr)
        except OSError:
            pass

        elapsed = time.monotonic() - loop_start
        remaining = dt - elapsed
        if remaining > 0:
            time.sleep(remaining)


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    t0 = threading.Thread(
        target=tank_loop, args=(0, 0.0, sock),
        daemon=True, name="tank-left"
    )
    t1 = threading.Thread(
        target=tank_loop, args=(1, PHASE_OFFSET_S, sock),
        daemon=True, name="tank-right"
    )

    t0.start()
    t1.start()

    print(f"SwimOn simulator -> UDP {UDP_HOST}:{UDP_PORT}  |  {SEND_RATE_HZ} Hz/arm")
    print(f"  Profile: {TARGET_SPM} SPM, peak force {PEAK_FORCE_N} N, peak {PEAK_RPM} RPM")
    print("Press Ctrl+C to stop.")
    try:
        t0.join()
    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
