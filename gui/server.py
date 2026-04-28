"""
server.py — SwimOn dashboard server

Listens for UDP packets from simulator.py (or the real ESP32), accumulates
workout metrics, and pushes them to the browser via WebSocket at 10 Hz.

Run: python server.py
Then open: http://localhost:5000
"""

import socket
import json
import time
import threading

from flask import Flask, render_template
from flask_socketio import SocketIO

# ── Configuration ─────────────────────────────────────────────────────────────
UDP_HOST   = "0.0.0.0"
UDP_PORT   = 9000
HTTP_HOST  = "0.0.0.0"
HTTP_PORT  = 5000
EMIT_HZ    = 10             # how often to push updates to the browser

# Swim distance: one arm stroke ≈ 0.75 m equivalent swimming distance
METERS_PER_STROKE = 0.75

# Calorie model:
#   mechanical energy → metabolic energy via efficiency and overhead factor
#   Arms-only ergometer: add METABOLIC_MULTIPLIER to account for breathing,
#   trunk stabilisation, and other whole-body costs not captured by arm drag alone.
MECHANICAL_EFFICIENCY = 0.25
METABOLIC_MULTIPLIER  = 3.5
J_PER_KCAL            = 4184.0

# Minimum RPM to consider a packet as an "active pull" for energy accounting
MIN_PULL_RPM = 3.0

# Rolling chart window: 10 s at 10 Hz = 100 points per tank
CHART_WINDOW = 100


# ── Workout state ──────────────────────────────────────────────────────────────
class WorkoutState:
    def __init__(self):
        self.lock = threading.Lock()
        self._init_fields()

    def _init_fields(self):
        self.running    = False
        self.start_time = None

        # Per-arm stroke detection state machine
        self.arm = [
            {"prev_dir": 0},
            {"prev_dir": 0},
        ]

        # Rolling chart buffers — list of {"t": elapsed_s, "v": value}
        self.chart_force = [[], []]   # index 0=left, 1=right
        self.chart_power = []         # combined power, both arms

        # Cumulative metrics
        self.total_strokes     = 0
        self.total_distance_m  = 0.0
        self.total_calories    = 0.0
        self.total_mech_J      = 0.0

        # Current-snapshot (last received packet per arm)
        self.cur_force  = [0.0, 0.0]
        self.cur_speed  = [0.0, 0.0]
        self.cur_power  = [0.0, 0.0]
        self.cur_rpm    = [0.0, 0.0]

    def reset(self):
        self._init_fields()

    def elapsed_s(self) -> float:
        if not self.running or self.start_time is None:
            return 0.0
        return time.monotonic() - self.start_time


# ── Packet processing ──────────────────────────────────────────────────────────
def process_packet(state: WorkoutState, pkt: dict) -> None:
    if not state.running:
        return

    tank     = int(pkt["tank"])      # 0 or 1
    dir_flag = int(pkt["dir"])       # 1=pull, -1=recovery, 0=stopped
    force_n  = float(pkt["force_n"])
    rpm      = float(pkt["rpm"])
    vel_ms   = abs(float(pkt["vel_ms"]))
    elapsed  = state.elapsed_s()
    dt       = 1.0 / EMIT_HZ        # assumed interval between packets per tank

    with state.lock:
        # Current-snapshot
        state.cur_force[tank] = force_n if dir_flag == 1 else 0.0
        state.cur_speed[tank] = vel_ms  if dir_flag == 1 else 0.0
        state.cur_rpm[tank]   = rpm
        power_w = force_n * vel_ms if (dir_flag == 1 and rpm > MIN_PULL_RPM) else 0.0
        state.cur_power[tank] = power_w

        # Rolling chart buffers
        pt_force = {"t": round(elapsed, 2), "v": round(state.cur_force[tank], 1)}
        pt_power = {"t": round(elapsed, 2), "v": round(power_w, 1)}

        state.chart_force[tank].append(pt_force)
        if len(state.chart_force[tank]) > CHART_WINDOW:
            state.chart_force[tank].pop(0)

        state.chart_power.append(pt_power)
        if len(state.chart_power) > CHART_WINDOW * 2:
            state.chart_power.pop(0)

        # Mechanical energy → calories (pull phase only)
        if dir_flag == 1 and rpm > MIN_PULL_RPM:
            state.total_mech_J  += power_w * dt
            state.total_calories = (
                state.total_mech_J * METABOLIC_MULTIPLIER
                / MECHANICAL_EFFICIENCY / J_PER_KCAL
            )

        # Stroke detection: pull → recovery transition = one arm stroke complete
        prev = state.arm[tank]["prev_dir"]
        if prev == 1 and dir_flag == -1:
            state.total_strokes    += 1
            state.total_distance_m  = state.total_strokes * METERS_PER_STROKE

        state.arm[tank]["prev_dir"] = dir_flag


# ── Background tasks ───────────────────────────────────────────────────────────
def udp_listener(state: WorkoutState, sio: SocketIO) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((UDP_HOST, UDP_PORT))
    sock.settimeout(1.0)
    print(f"UDP listener bound to {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(2048)
            pkt = json.loads(data.decode())
            process_packet(state, pkt)
        except socket.timeout:
            pass
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        except Exception as e:
            print(f"UDP error: {e}")


def emit_loop(state: WorkoutState, sio: SocketIO) -> None:
    while True:
        time.sleep(1.0 / EMIT_HZ)

        with state.lock:
            elapsed  = state.elapsed_s()
            minutes  = int(elapsed) // 60
            seconds  = int(elapsed) % 60

            payload = {
                "time_str":    f"{minutes:02d}:{seconds:02d}",
                "elapsed_s":   round(elapsed, 1),
                "distance_m":  round(state.total_distance_m, 1),
                "calories":    round(state.total_calories, 2),
                "strokes":     state.total_strokes,
                "speed_ms":    round((state.cur_speed[0] + state.cur_speed[1]) / 2.0, 2),
                "power_w":     round(state.cur_power[0] + state.cur_power[1], 1),
                "force_left":  round(state.cur_force[0], 1),
                "force_right": round(state.cur_force[1], 1),
                "chart_left":  list(state.chart_force[0]),
                "chart_right": list(state.chart_force[1]),
                "chart_power": list(state.chart_power[-CHART_WINDOW:]),
                "running":     state.running,
            }

        sio.emit("metrics", payload)


# ── Flask app ──────────────────────────────────────────────────────────────────
app   = Flask(__name__)
app.config["SECRET_KEY"] = "swimon-2025"
sio   = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
state = WorkoutState()


@app.route("/")
def index():
    return render_template("index.html")


@sio.on("connect")
def on_connect():
    print("Browser connected")


@sio.on("disconnect")
def on_disconnect():
    print("Browser disconnected")


@sio.on("begin_workout")
def on_begin():
    with state.lock:
        state.reset()
        state.running    = True
        state.start_time = time.monotonic()
    print("Workout started")


@sio.on("end_workout")
def on_end():
    with state.lock:
        state.running = False
    print("Workout ended")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sio.start_background_task(udp_listener, state, sio)
    sio.start_background_task(emit_loop,    state, sio)
    print(f"SwimOn server -> http://localhost:{HTTP_PORT}")
    sio.run(app, host=HTTP_HOST, port=HTTP_PORT, debug=False, allow_unsafe_werkzeug=True)
