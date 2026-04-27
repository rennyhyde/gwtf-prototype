# Mechanical Design: Quadrature Optical Encoder

## Overview

This document describes the physical design of the rotating disc and dual-beam IR
sensor arrangement used to measure pulley speed and direction for the swimon
biomechanics system.

The encoder uses **quadrature encoding**: two IR emitter/receiver pairs (channels A
and B) are offset from each other by 90° of the slit period. Whichever channel
triggers first indicates the direction of rotation. Speed is derived from the
frequency of quadrature steps.

---

## Disc Design

### Slit Count

The disc has **8 equally-spaced identical slits**.

| Parameter | Value |
|-----------|-------|
| Number of slits | 8 |
| Angular spacing between slit centers | 45° |
| Slit width (angular) | ~10–15° of arc |
| Gap width (angular) | ~30–35° of arc |

With 8 slits and quadrature encoding (4 steps per slit), the encoder produces
**32 steps per revolution**. At 60 RPM this gives 32 Hz of step events — well
suited for swimming stroke dynamics (~0.5–2 Hz).

### Slit Width Guidelines

- Slit width should be **narrower than the gap** so the beam is blocked for less
  than half the period. A 10–15° slit at 45° spacing is a good starting point.
- Slits that are too narrow risk being missed by the comparator or at high speed.
- Slits that are too wide (>22.5°) reduce the quadrature margin and can cause
  ambiguous decoding.

### Disc Radius and Material

- Mount the disc concentrically on the pulley shaft.
- The IR sensors should read at a radius where the slit features are cleanly
  machined. A radius of **20–40 mm** is practical for most pulley sizes.
- Use opaque material (aluminium, black acetal, or thick card stock sprayed
  matte black) to ensure full beam blocking.

---

## Sensor Placement

### Quadrature Offset

For correct quadrature encoding, channels A and B must be physically separated so
that their signals are 90° out of phase with each other (a quarter of one slit
period).

```
Quarter-period angle = (360° / slits) / 4
                     = 45° / 4
                     = 11.25°
```

Physical arc separation at a given sensor radius `r`:

```
arc_separation = 2π × r × (11.25 / 360)
               = r × 0.1963
```

| Sensor radius `r` | Physical separation |
|-------------------|---------------------|
| 20 mm             | 3.9 mm              |
| 25 mm             | 4.9 mm              |
| 30 mm             | 5.9 mm              |
| 35 mm             | 6.9 mm              |
| 40 mm             | 7.9 mm              |

Mount sensors A and B at the same radius, separated by this arc distance along the
circumference. Both sensors must be at the same axial position (same distance from
the disc face).

### Sensor Polarity

The LM339 comparator circuit is wired so that:

- **HIGH** (3.3 V) = beam **interrupted** (slit is passing through the sensor)
- **LOW** (0 V) = beam **clear** (no slit, emitter light reaches receiver)

This is reflected in the firmware's quadrature table and interrupt handler.

### Physical Assembly Notes

- Both IR emitter/receiver pairs must be identical (same part number, same forward
  current resistor) so their output voltages match the shared LM339 reference
  voltage.
- Align sensors so the beam path is perpendicular to the disc face and passes
  through the center of the slit slot.
- Allow ~1–2 mm of clearance between the disc edge and the sensor housing to
  avoid contact during vibration.
- If the disc wobbles axially during rotation, widen the sensor gap accordingly.

---

## Signal Chain

```
IR emitter  ──[resistor]── 5V
                                      ┌── LM339 IN+ (ch A)
IR receiver A ── analog out ──────────┤
                                      └── LM339 IN- (shared Vref)

IR receiver B ── analog out ──────────── LM339 IN+ (ch B)

LM339 ch A OUT ──[10 kΩ pull-up to 3.3V]── ESP32-S3 GPIO10 (channel A)
LM339 ch B OUT ──[10 kΩ pull-up to 3.3V]── ESP32-S3 GPIO9  (channel B)
```

Both comparator outputs are open-collector: they sink to GND when the beam is
interrupted, and float to 3.3 V via the pull-up when the beam is clear — **inverted**
from what the comparator input sees. The LM339 is configured with the IR receiver
on IN+ and the reference on IN-, so:

- Beam clear: IR out > Vref → comparator output = high-Z → GPIO = HIGH (pulled up)
- Beam interrupted: IR out < Vref → comparator sinks output → GPIO = LOW

Wait — this gives **LOW = interrupted**, but the firmware expects **HIGH = interrupted**.
Resolve by either:
1. Swap IN+ and IN− on the LM339 (reference on IN+, IR on IN−), which inverts the
   output so that slit-present = HIGH = open-collector not sinking = pull-up to 3.3V.
2. Or wire as described and invert the polarity in the QUAD_TABLE firmware constants.

**Recommended wiring for HIGH = interrupted:**
```
LM339 IN+  ← Vref (resistor divider)
LM339 IN−  ← IR receiver analog out

When beam clear:   IR out > Vref → IN- > IN+ → output LOW  → GPIO = LOW  (pulled to GND)
When beam blocked: IR out < Vref → IN- < IN+ → output HIGH-Z → GPIO = HIGH (3.3V pull-up)
```

This matches the firmware convention: `HIGH = beam interrupted (slit present)`.

---

## Mapping to Biomechanical Quantities

### Angular Velocity

```
ω (rad/s) = (2π × RPM) / 60
           = (2π × step_rate) / STEPS_PER_REV
```

where `step_rate` is quadrature steps per second.

### Rope Linear Velocity (hand proxy)

```
v_rope (m/s) = ω × r_pulley
```

where `r_pulley` is the pulley shaft radius in metres.

### Torque and Force

```
τ_total (N·m) = I × α + C × ω²

F_rope  (N)   = τ_total / r_pulley
```

Parameters to characterize per machine unit:

| Parameter | Symbol | How to measure |
|-----------|--------|----------------|
| Flywheel moment of inertia | `I` (kg·m²) | Free-spin deceleration fit: `I dω/dt = -C ω²` |
| Flywheel drag coefficient | `C` | Same free-spin test |
| Pulley radius | `r_pulley` (m) | Direct measurement |
| Spring constant | `k` (N·m/rad) | Static torque vs. angle measurement |

### Stroke Segmentation

Direction transitions (CW ↔ CCW) mark stroke boundaries:
- **CW** (or whichever physical direction is the power stroke) = propulsive phase
- **CCW** = return phase (spring-driven)

The firmware's rolling direction vote (`dir_votes[8]`) provides a noise-resistant
direction signal with a latency of ~8 quadrature steps = 1 slit crossing.

---

## Adjusting for Different Disc Configurations

To change the slit count, update only `SLITS_PER_REV` in `main.rs`:

```rust
const SLITS_PER_REV: u32 = 8;  // change this
const STEPS_PER_REV: u32 = SLITS_PER_REV * 4;  // derived automatically
```

Then re-compute the physical sensor separation using the formula above.

| Slits | Steps/rev | Step rate at 60 RPM | Sensor separation at r=30mm |
|-------|-----------|----------------------|-----------------------------|
| 4     | 16        | 16 Hz                | 11.8 mm                     |
| 8     | 32        | 32 Hz                | 5.9 mm                      |
| 16    | 64        | 64 Hz                | 2.9 mm                      |

Fewer slits are easier to cut but give coarser speed resolution. 8 slits is a good
balance for swimming stroke rates (~0.5–1.5 Hz stroke cycles needing ~10 Hz sensor data).
