# Freestyle Swimming Simulator — Validation System Engineering Context

## Project Goal

Build an electronic validation system for a **freestyle swimming ergometer (dry-land simulator)**. The system must acquire sensor data from the machine, compute biomechanical metrics, and compare them against real in-water swimmer biomechanical data from the published literature.

---

## Machine Hardware Description

### Resistance Mechanism
- **Water-tank flywheel** provides hydrodynamic resistance. Resistance torque is velocity-dependent (τ ∝ ω²), analogous to real hand drag in water.
- A **rope and pulley** transmit force from the user's paddle/handle to the flywheel axle.
- A **torsional return spring** retracts the paddle at the end of the power stroke (simulates the arm recovery phase). The spring engages when the rope goes slack as the user transitions from power stroke to return.

### Sensor: Dual-Slit IR Photogate
- A **tab is mounted on the pulley** and passes through an IR photogate (emitter/detector pair) once per revolution.
- The tab has **two slits of different widths** cut into it:
  - **Slit A (narrow):** ~3–5° arc width → produces a short pulse
  - **Slit B (wide):** ~10–15° arc width → produces a long pulse
  - Slits are **separated by ~30–45°** so they never overlap at realistic angular velocities
- **Direction detection:** The order in which Slit A and Slit B fire (A→B vs. B→A) indicates whether the pulley is rotating in the power-stroke direction or the return direction.
- **Angular velocity (ω):** Computed from the time between rising edges (or falling edges) of the slit pulses using the known angular spacing between slits and slit widths.

### Known Physical Parameters (to be measured/characterized per machine unit)
- `r` — pulley radius (m), maps rope linear velocity to angular velocity: `v_rope = ω * r`
- `I` — flywheel moment of inertia (kg·m²)
- `k` — torsional spring constant (N·m/rad)
- `C_flywheel` — flywheel drag coefficient (characterizes τ ∝ ω² relationship, to be empirically determined)

---

## Sensor Signal Processing Pipeline

### Raw Signal → Angular Velocity
1. Microcontroller (e.g., STM32, Arduino, Teensy) captures **timestamps** of rising and falling edges from the photogate output.
2. From the known arc widths of Slit A and Slit B and the inter-slit angular separation, compute ω for each pulse event:
   - `ω = Δθ / Δt` where Δθ is the known angular span and Δt is the measured time between edges.
3. **Direction flag** set based on pulse order: A-before-B = power stroke; B-before-A = return stroke.
4. Output: a time-series `[t, ω, direction]` at each slit-crossing event.

### Angular Velocity → Force
The torque on the flywheel is:
```
τ_total = I * α + C_flywheel * ω²
```
where α = dω/dt (numerical derivative, smoothed).

Rope tension (≈ hand force on paddle):
```
F = τ_total / r
```

### Derived Metrics Per Stroke
- **Peak force** (N): max F during power-stroke window
- **Mean force** (N): average F over the power stroke
- **Impulse** (N·s): ∫F dt over power stroke duration
- **Peak hand velocity** (m/s): max(ω * r) during power stroke
- **Stroke duration** (s): time from power stroke start to end (direction change)
- **Stroke rate** (strokes/min): 60 / stroke_cycle_period
- **Power** (W): P = τ * ω, averaged over power stroke
- **Rate of Force Development (RFD)** (N/s): slope of F(t) on the rising edge

---

## Biomechanical Reference Data (from Literature)

### Force Benchmarks by Skill Level
| Population | Mean Force | Peak Force | Source |
|---|---|---|---|
| Recreational/triathlete (~0.8–0.9 m/s) | 20–40 N | 35–50 N | Barbosa et al. 2020 (PMC7242395) |
| Competitive (tethered, maximal) | ~39 N | ~158 N | Barbosa et al. 2020 |
| US Olympic champion | — | ~134–175 N | Takagi & Sanders 2002; tethered data |
| Elite sprinter (SPH model, 1.45–1.47 m/s) | — | 250–300 N | Barbosa et al. 2020 |

### Power Benchmarks
- Elite male front crawl sprinters at 2.20 ± 0.07 m/s: **399 ± 56 W** (Gatta et al. 2016, PLOS ONE PMC5031421)
- Power from thrust equals power to overcome drag at constant speed: `Ft * v = Fd * v`

### Stroke Rate Benchmarks
| Level | Stroke Rate | Swim Velocity |
|---|---|---|
| 200 m pace (all levels) | ~40 strokes/min | moderate |
| Elite male sprint | >50 strokes/min | >1.8 m/s |

### Hand Velocity
- Competitive swimmers: peak hand speed ~2–4 m/s during pull phase
- Mean swimming velocity correlates with hand speed at r = 0.881

### Force-Time Curve Shape (Normalized)
- Force rises from ~0% to peak at approximately **40–60% of the power-stroke duration**
- Roughly bell-shaped; rise is faster than decay
- The **impulse** (area under curve) is more predictive of performance than peak force alone
- The **rate of force development (RFD)** is a key metric; both mean RFD and max RFD over 5-second windows correlate highly with 25m–100m freestyle speed

### Intra-Cycle Velocity Profile
- Front crawl shows a **dual-peak** velocity profile per full stroke cycle
- Higher peaks correspond to the power phases of each arm
- Lower peaks correspond to the leg kick contribution

### Stroke Phase Structure (Normalized to 0–100% of power stroke)
Following Chollet et al.'s Index of Coordination (IdC) framework:
1. **Catch** (0–~15%): hand entry, high-elbow setup, force rising from zero
2. **Pull/Insweep** (~15–60%): main propulsive phase, peak force here
3. **Push/Upsweep** (~60–85%): force maintained then declining, triceps-dominated
4. **Release** (~85–100%): hand exits, force drops to near zero

The **Insweep/pull phase is the most important** for force and kinematic variables.

---

## Validation Logic

### Layer 1 — Scalar Benchmarks
Compare computed scalars against the reference table above:
- `peak_force` in [20, 300] N (flag if outside range for stated user level)
- `stroke_rate` in [30, 60] strokes/min
- `peak_velocity` in [1.0, 4.5] m/s (rope = hand proxy)
- `mean_power` in [50, 500] W

### Layer 2 — Normalized Force-Time Curve Shape
1. Segment each stroke using the direction flag (power stroke window only).
2. Normalize time axis to 0–100% of stroke duration.
3. Normalize force axis to 0–1 (peak = 1.0).
4. Compare against a reference curve (bell-shaped, peak ~50%). 
5. Metrics: RMS error vs. reference, time-of-peak as % of stroke, rise-time (0→90% peak), fall-time (90%→0 on decay).

### Layer 3 — Power and Impulse
- Compute `impulse = ∫F dt` per stroke (N·s)
- Compute `mean_power = impulse * mean_velocity / stroke_duration` (W) — or directly from `P = τ * ω`
- Compare against published power values, normalized to user body mass if desired (W/kg)

### Symmetry Check (if two paddles/sensors used)
- Left vs. right arm impulse asymmetry: flag if >10% difference (literature shows asymmetry correlates negatively with performance)

---

## Reference Papers (must-read for implementation)

1. **Barbosa et al. (2020)** — "Arm-pull thrust in human swimming and the effect of post-activation potentiation." *Scientific Reports.* PMC7242395. → Primary force benchmark data.

2. **Gatta et al. (2016)** — "The Relationship between Power Generated by Thrust and Power to Overcome Drag in Elite Short Distance Swimmers." *PLOS ONE.* PMC5031421. → Power validation; tethered ↔ free-swimming equivalence.

3. **Takagi & Sanders (2002)** — "Measurement of propulsion by the hand during competitive swimming." → 3D kinematics vs. MAD system cross-validation; only 5% (2 N) mean difference between methods.

4. **Chollet et al. (2000)** — "A new index of coordination for the crawl." *Int. J. Sports Med.* → Defines stroke phase segmentation and the Index of Coordination (IdC).

5. **Morouço et al. (2014)** — "Tethered Swimming Can Be Used to Evaluate Force Contribution for Short-Distance Swimming Performance." → Validates tethered-force measurement against free-swimming; mean force (r=0.85) and force oscillation (r=0.86) correlate with swim velocity.

6. **Tsunokawa et al. (2019/2022)** / **Frontiers in Sports** paper (DOI: 10.3389/fspor.2022.786459) — Hand kinematics, hydrodynamic pressure, and propulsive force in sprint front crawl. → Hand speed ↔ force ↔ swim velocity correlations.

7. **Bilinauskaite et al. (2013)** — "CFD Study of Swimmer's Hand Velocity, Orientation, and Shape." *BioMed Research International.* PMC3638706. → Drag force ∝ velocity²; hand orientation effects. Informs flywheel resistance model.

---

## System Architecture Recommendation

```
[IR Photogate] 
     ↓ digital pulse stream
[Microcontroller] — timestamp edges, output [t, ω, direction] over serial/USB
     ↓ structured data stream
[Host Application (Python or C++)]
  ├── Signal processor: ω(t) → F(t), P(t)
  ├── Stroke segmenter: uses direction flag to identify stroke boundaries
  ├── Metrics extractor: peak F, mean F, impulse, RFD, power, stroke rate
  ├── Normalizer: time-normalize each stroke to 0–100%
  ├── Validator: compare vs. reference ranges and reference F(t) curve
  └── Output: per-stroke report, real-time dashboard, CSV log
```

### Suggested Tech Stack
- **Firmware:** C/C++ (Arduino/PlatformIO or STM32 HAL) — edge timestamping at microsecond resolution
- **Host:** Python with NumPy/SciPy for signal processing, Pandas for logging, Matplotlib or Plotly for visualization
- **Serial protocol:** Simple binary or JSON frames: `{"t_us": 123456, "edge": "rising", "slit": "A"}`
- **Calibration routine:** Spin flywheel at known RPM (optical tach or known motor), log photogate output to fit C_flywheel and verify I

---

## Key Implementation Notes

### Slit Width Ambiguity at Low ω
At very low angular velocities (slow strokes), the pulse widths from Slit A and Slit B may be long enough to be ambiguous. Implement a **minimum ω threshold** (e.g., ω_min = 0.5 rad/s) below which direction detection is marked uncertain.

### Return Spring Dynamics
During the return stroke (spring-driven, user relaxing), the flywheel decelerates due to:
- Water drag (τ ∝ ω²)
- Spring restoring torque (τ_spring = k * θ)
Only the **power stroke window** (direction flag = forward) is used for biomechanical scoring. The return stroke data can be used to estimate spring constant k by fitting the deceleration curve.

### Flywheel Characterization (Mandatory Before Deployment)
Run a free-spin deceleration test (spin flywheel, disengage rope, log ω(t)) to fit:
```
I * dω/dt = -C * ω²
```
Solve for C and I. This calibration is per-unit and must be stored in firmware/config.

### Noise and Differentiation
Numerical differentiation of ω(t) to get α(t) amplifies noise. Use a **Savitzky-Golay filter** or a **causal low-pass filter** (e.g., 2nd-order Butterworth, cutoff ~10 Hz) on ω before differentiating. Stroke rates are ~0.5–1 Hz; hand velocity peaks are ~2–4 Hz; keep bandwidth to ~15 Hz.

---

## Validation Output Format (Suggested)

Per stroke, output:
```json
{
  "stroke_id": 42,
  "direction": "power",
  "duration_s": 0.48,
  "stroke_rate_spm": 38.2,
  "peak_force_N": 87.3,
  "mean_force_N": 52.1,
  "impulse_Ns": 25.0,
  "peak_velocity_ms": 2.31,
  "mean_power_W": 120.4,
  "RFD_Ns": 410.0,
  "time_of_peak_pct": 47.2,
  "validation": {
    "force_in_range": true,
    "stroke_rate_in_range": true,
    "velocity_in_range": true,
    "power_in_range": true,
    "curve_rms_error": 0.082
  }
}
```