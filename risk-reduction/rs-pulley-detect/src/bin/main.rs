#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
    holding buffers for the duration of a data transfer."
)]

use core::cell::RefCell;

use critical_section::Mutex;
use esp_backtrace as _;
use esp_hal::{
    gpio::{Event, Input, InputConfig, Level, Output, OutputConfig},
    handler,
    interrupt::{self, Priority},
    main,
    peripherals::Interrupt,
    time::{Duration, Instant},
};
use esp_println::println;

esp_bootloader_esp_idf::esp_app_desc!();

// ---------------------------------------------------------------------------
// Hardware configuration — adjust if you rewire
// ---------------------------------------------------------------------------
// GPIO2  = IR channel A output from LM339
// GPIO3  = IR channel B output from LM339
// GPIO6  = onboard LED (debug indicator)
//
// Signal polarity (set by your comparator circuit):
//   HIGH = beam interrupted (slit is passing through)
//   LOW  = beam clear (no slit)

// ---------------------------------------------------------------------------
// Encoder parameters — update to match your physical disc
// ---------------------------------------------------------------------------

// Number of equally-spaced slits cut into the rotating disc.
// Each slit produces 4 quadrature steps (A_rise, B_rise, A_fall, B_fall).
const SLITS_PER_REV: u32 = 8;
const STEPS_PER_REV: u32 = SLITS_PER_REV * 4;

// Ignore any quadrature step that arrives sooner than this after the previous
// one — filters out comparator chatter and electrical noise.
const MIN_STEP_US: u64 = 200;

// If no step arrives within this window, report the disc as stalled.
const STALL_TIMEOUT_US: u64 = 3_000_000; // 3 seconds

// How many consecutive same-direction steps are required before direction is
// confirmed and printed. 4 steps = 1/8 of a revolution (at 8 slits), which at
// 30 RPM resolves in ~67 ms. Once confirmed, direction is held until the
// opposite direction accumulates this many consecutive steps (hysteresis).
const DIR_CONFIRM_STEPS: u8 = 4;

// Set to true to print each raw quadrature step to serial. Use this to
// diagnose physical sensor placement: during a single-direction manual spin
// you should see all +1 or all -1. Alternating +1/-1 means your sensors are
// ~180° out of phase (half a slit period apart) instead of 90°.
const DEBUG_STEPS: bool = false;

// ---------------------------------------------------------------------------
// Quadrature decode table
//
// Index as QUAD_TABLE[prev_ab][curr_ab] where curr_ab = (A_level << 1) | B_level.
// A_level and B_level are 1 when the beam is interrupted (slit present), 0 when clear.
//
//  +1  → valid CW  step
//  -1  → valid CCW step
//   0  → no change (spurious interrupt, both channels same level)
//   2  → error: diagonal jump (missed an edge — increment error counter)
//
// CW  sequence of curr_ab values: 00 → 10 → 11 → 01 → 00
// CCW sequence of curr_ab values: 00 → 01 → 11 → 10 → 00
// ---------------------------------------------------------------------------
#[rustfmt::skip]
const QUAD_TABLE: [[i8; 4]; 4] = [
    [ 0, -1,  1,  2],  // prev = 00 (A=0, B=0)
    [ 1,  0,  2, -1],  // prev = 01 (A=0, B=1)
    [-1,  2,  0,  1],  // prev = 10 (A=1, B=0)
    [ 2,  1, -1,  0],  // prev = 11 (A=1, B=1)
];

// ---------------------------------------------------------------------------
// Shared state between the GPIO ISR and the main loop
// ---------------------------------------------------------------------------

struct EncoderState {
    // 2-bit Gray-code state from the previous edge: (A_level << 1) | B_level
    prev_ab: u8,

    // Timestamp of the most recent valid quadrature step (None until first step).
    last_step: Option<Instant>,

    // Ring buffer of the last 16 inter-step periods (µs).
    // Used to compute a rolling average for RPM.
    periods: [u64; 16],
    period_sum: u64, // sum of all valid entries in `periods`
    period_head: usize,
    period_count: usize,

    // Streak-based direction detector with hysteresis.
    // Direction is confirmed only after DIR_CONFIRM_STEPS consecutive same-
    // direction steps, and held until the opposite direction is confirmed.
    consecutive_dir: i8,   // direction of the current streak (+1 or -1)
    consecutive_count: u8, // length of current streak
    resolved_dir: i8,      // last confirmed direction; 0 until first confirmation

    // Diagnostic: count of diagonal jumps (missed edges).
    errors: u32,
}

impl EncoderState {
    const fn new() -> Self {
        Self {
            prev_ab: 0,
            last_step: None,
            periods: [0u64; 16],
            period_sum: 0,
            period_head: 0,
            period_count: 0,
            consecutive_dir: 0,
            consecutive_count: 0,
            resolved_dir: 0,
            errors: 0,
        }
    }

    // Record a valid step, update the rolling period buffer, and advance the
    // streak-based direction detector.
    fn push_step(&mut self, now: Instant, step: i8) {
        if let Some(prev) = self.last_step {
            let period_us = (now - prev).as_micros();

            // Evict the oldest entry when the ring buffer is full.
            if self.period_count == 16 {
                self.period_sum = self.period_sum.saturating_sub(self.periods[self.period_head]);
            }
            self.periods[self.period_head] = period_us;
            self.period_sum = self.period_sum.saturating_add(period_us);
            self.period_head = (self.period_head + 1) % 16;
            if self.period_count < 16 {
                self.period_count += 1;
            }
        }
        self.last_step = Some(now);

        // Streak detector: extend the current streak if same direction,
        // otherwise restart it. Confirm direction once the streak is long enough.
        if step == self.consecutive_dir {
            self.consecutive_count = self.consecutive_count.saturating_add(1);
        } else {
            self.consecutive_dir = step;
            self.consecutive_count = 1;
        }
        if self.consecutive_count >= DIR_CONFIRM_STEPS {
            self.resolved_dir = self.consecutive_dir;
        }
    }

    // Returns RPM × 100 (fixed-point, 2 decimal places) to avoid floating point.
    // Returns 0 if there is not yet enough data.
    fn rpm_x100(&self) -> u32 {
        if self.period_count == 0 {
            return 0;
        }
        let avg_us = self.period_sum / self.period_count as u64;
        if avg_us == 0 {
            return 0;
        }
        // RPM × 100 = 6_000_000_000 / (avg_step_us × STEPS_PER_REV)
        (6_000_000_000u64 / (avg_us * STEPS_PER_REV as u64)) as u32
    }

    // Returns the last confirmed direction: +1 CW, -1 CCW, 0 not yet resolved.
    fn direction(&self) -> i8 {
        self.resolved_dir
    }
}

// A small token posted to the main loop after each valid step so the main
// loop can print without holding the encoder lock.
#[derive(Copy, Clone)]
struct StepEvent {
    step: i8,
}

// ---------------------------------------------------------------------------
// Statics shared between ISR and main loop
// ---------------------------------------------------------------------------

static ENCODER: Mutex<RefCell<EncoderState>> =
    Mutex::new(RefCell::new(EncoderState::new()));

static PENDING_STEP: Mutex<RefCell<Option<StepEvent>>> =
    Mutex::new(RefCell::new(None));

// The GPIO pins must be accessible from the ISR. We store them here after
// calling `listen()` in main(). `Input<'static>` is valid because the
// peripheral tokens from `esp_hal::init()` live for the duration of the
// program (main() never returns).
static PIN_A: Mutex<RefCell<Option<Input<'static>>>> =
    Mutex::new(RefCell::new(None));
static PIN_B: Mutex<RefCell<Option<Input<'static>>>> =
    Mutex::new(RefCell::new(None));

// ---------------------------------------------------------------------------
// GPIO interrupt handler
//
// Both GPIO2 (channel A) and GPIO3 (channel B) share the single ESP32-S3
// GPIO peripheral interrupt vector. On every edge (either channel), we read
// both pin levels, look up the quadrature transition, and post a StepEvent.
// ---------------------------------------------------------------------------
#[handler]
fn gpio_interrupt() {
    critical_section::with(|cs| {
        // Read and clear interrupts, capture both channel levels in one
        // critical section so the two reads are consistent.
        let (a_level, b_level, any_fired) = {
            let mut borrow_a = PIN_A.borrow(cs).borrow_mut();
            let mut borrow_b = PIN_B.borrow(cs).borrow_mut();
            match (borrow_a.as_mut(), borrow_b.as_mut()) {
                (Some(pa), Some(pb)) => {
                    let a_fired = pa.is_interrupt_set();
                    let b_fired = pb.is_interrupt_set();
                    if a_fired {
                        pa.clear_interrupt();
                    }
                    if b_fired {
                        pb.clear_interrupt();
                    }
                    let a = if pa.level() == Level::High { 1u8 } else { 0u8 };
                    let b = if pb.level() == Level::High { 1u8 } else { 0u8 };
                    (a, b, a_fired || b_fired)
                }
                _ => (0, 0, false),
            }
        }; // pin borrows dropped here

        if !any_fired {
            return;
        }

        let now = Instant::now();
        let curr_ab = (a_level << 1) | b_level;

        let mut enc = ENCODER.borrow(cs).borrow_mut();
        let prev_ab = enc.prev_ab;
        let step = QUAD_TABLE[prev_ab as usize][curr_ab as usize];
        enc.prev_ab = curr_ab;

        match step {
            2 => {
                // Missed an edge — diagonal jump in the Gray code.
                enc.errors += 1;
            }
            0 => {
                // No state change; spurious trigger.
            }
            s => {
                // Valid CW (+1) or CCW (-1) step.
                let too_fast = enc
                    .last_step
                    .map(|prev| (now - prev).as_micros() < MIN_STEP_US)
                    .unwrap_or(false);

                if !too_fast {
                    enc.push_step(now, s);
                    if DEBUG_STEPS {
                        // Prints each step for diagnosing sensor offset.
                        // Expected for a single-direction spin: all +1 or all -1.
                        // Alternating +1/-1 means sensors are ~180° out of phase.
                        println!("step:{:+} ab:{:02b}->{:02b}", s, prev_ab, curr_ab);
                    }
                    // Post event for main loop (PIN_A/B borrows already released).
                    *PENDING_STEP.borrow(cs).borrow_mut() = Some(StepEvent { step: s });
                }
            }
        }
    });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------
#[main]
fn main() -> ! {
    let peripherals = esp_hal::init(esp_hal::Config::default());

    println!("swimon pulley-detect starting");

    // Debug LED — set HIGH briefly on startup to confirm firmware is running.
    let mut led = Output::new(peripherals.GPIO6, Level::Low, OutputConfig::default());
    led.set_high();
    let t = Instant::now();
    while t.elapsed() < Duration::from_millis(200) {}
    led.set_low();

    // Configure channel A (GPIO2) and channel B (GPIO3) as digital inputs.
    // No internal pull — the LM339 comparator circuit with 10 kΩ pull-up to
    // 3.3 V provides a defined voltage on both lines.
    let mut pin_a = Input::new(peripherals.GPIO2, InputConfig::default());
    let mut pin_b = Input::new(peripherals.GPIO3, InputConfig::default());

    // Enable edge interrupts on both channels before moving pins into statics.
    pin_a.listen(Event::AnyEdge);
    pin_b.listen(Event::AnyEdge);

    // Move pins into the statics so the ISR can access them.
    critical_section::with(|cs| {
        *PIN_A.borrow(cs).borrow_mut() = Some(pin_a);
        *PIN_B.borrow(cs).borrow_mut() = Some(pin_b);
    });

    // Register the GPIO interrupt handler and enable it.
    unsafe {
        interrupt::bind_interrupt(Interrupt::GPIO, gpio_interrupt.handler());
    }
    interrupt::enable(Interrupt::GPIO, Priority::Priority1).unwrap();

    println!("interrupts enabled — waiting for pulley rotation");
    println!(">rpm:0.00");
    println!(">dir:STOP");

    let mut last_print = Instant::now();
    let print_interval = Duration::from_millis(100); // 10 Hz output rate

    loop {
        // Consume any pending step event posted by the ISR.
        let _step = critical_section::with(|cs| PENDING_STEP.borrow(cs).borrow_mut().take());

        // Print at a fixed 10 Hz rate regardless of step arrival rate.
        if last_print.elapsed() >= print_interval {
            last_print = Instant::now();

            let (rpm_x100, dir, errors, last_step) = critical_section::with(|cs| {
                let enc = ENCODER.borrow(cs).borrow();
                (enc.rpm_x100(), enc.direction(), enc.errors, enc.last_step)
            });

            // Check for stall: no step received in STALL_TIMEOUT_US.
            let stalled = last_step
                .map(|t| t.elapsed().as_micros() > STALL_TIMEOUT_US)
                .unwrap_or(true);

            if stalled {
                println!(">rpm:0.00");
                println!(">dir:STOP");
            } else {
                // Print RPM as fixed-point with 2 decimal places.
                println!(">rpm:{}.{:02}", rpm_x100 / 100, rpm_x100 % 100);
                let dir_str = match dir {
                    1 => "CW",
                    -1 => "CCW",
                    _ => "UNK",
                };
                println!(">dir:{}", dir_str);
            }

            // Emit error counter for diagnostics. Should stay 0 during normal
            // operation; rising values indicate missed edges (noise or sensor
            // misalignment).
            if errors > 0 {
                println!(">errors:{}", errors);
            }
        }
    }
}
