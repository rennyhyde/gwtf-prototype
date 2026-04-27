// ---------------------------------------------------------------------------
// DRAFT: Inter-sensor timing direction detection
//
// This approach replaces the QUAD_TABLE with a time-based comparison:
// on each channel-A rising edge, we measure how long ago channel B last rose.
// With sensors ~27% of a slit period apart:
//   - since_b < period/2  →  +1 (one direction)
//   - since_b > period/2  →  -1 (other direction)
//
// Status: compiled but showed no speed output on hardware.
// Root cause not yet determined. Possible issues to investigate:
//   1. A rising edges (LOW→HIGH) not occurring — check if polarity is inverted
//      (HIGH = slit open / beam clear on the actual circuit).
//   2. b_rose && a_rose guard rejecting too many events.
//   3. prev_ab state getting out of sync when both pins transition near-simultaneously.
//   4. DEBUG_STEPS = true flooding serial output (was set by linter).
//
// Disc: 8 slits × 15° wide, 30° gaps → 45° period, sensors ~13° apart.
// ---------------------------------------------------------------------------

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

const SLITS_PER_REV: u32 = 8;
const MIN_STEP_US: u64 = 500;
const STALL_TIMEOUT_US: u64 = 3_000_000;
const DIR_CONFIRM_STEPS: u8 = 4;
const DEBUG_STEPS: bool = false;

struct EncoderState {
    prev_ab: u8,
    last_a_rise: Option<Instant>,
    last_b_rise: Option<Instant>,
    periods: [u64; 8],
    period_sum: u64,
    period_head: usize,
    period_count: usize,
    consecutive_dir: i8,
    consecutive_count: u8,
    resolved_dir: i8,
    errors: u32,
}

impl EncoderState {
    const fn new() -> Self {
        Self {
            prev_ab: 0,
            last_a_rise: None,
            last_b_rise: None,
            periods: [0u64; 8],
            period_sum: 0,
            period_head: 0,
            period_count: 0,
            consecutive_dir: 0,
            consecutive_count: 0,
            resolved_dir: 0,
            errors: 0,
        }
    }

    fn rpm_x100(&self) -> u32 {
        if self.period_count == 0 {
            return 0;
        }
        let avg_us = self.period_sum / self.period_count as u64;
        if avg_us == 0 {
            return 0;
        }
        (6_000_000_000u64 / (avg_us * SLITS_PER_REV as u64)) as u32
    }

    fn direction(&self) -> i8 {
        self.resolved_dir
    }
}

#[derive(Copy, Clone)]
struct StepEvent {
    step: i8,
}

static ENCODER: Mutex<RefCell<EncoderState>> =
    Mutex::new(RefCell::new(EncoderState::new()));

static PENDING_STEP: Mutex<RefCell<Option<StepEvent>>> =
    Mutex::new(RefCell::new(None));

static PIN_A: Mutex<RefCell<Option<Input<'static>>>> =
    Mutex::new(RefCell::new(None));
static PIN_B: Mutex<RefCell<Option<Input<'static>>>> =
    Mutex::new(RefCell::new(None));

#[handler]
fn gpio_interrupt() {
    critical_section::with(|cs| {
        let (a_level, b_level, a_fired, b_fired) = {
            let mut borrow_a = PIN_A.borrow(cs).borrow_mut();
            let mut borrow_b = PIN_B.borrow(cs).borrow_mut();
            match (borrow_a.as_mut(), borrow_b.as_mut()) {
                (Some(pa), Some(pb)) => {
                    let af = pa.is_interrupt_set();
                    let bf = pb.is_interrupt_set();
                    if af { pa.clear_interrupt(); }
                    if bf { pb.clear_interrupt(); }
                    let a = if pa.level() == Level::High { 1u8 } else { 0u8 };
                    let b = if pb.level() == Level::High { 1u8 } else { 0u8 };
                    (a, b, af, bf)
                }
                _ => (0, 0, false, false),
            }
        };

        if !a_fired && !b_fired { return; }

        let now = Instant::now();
        let curr_ab = (a_level << 1) | b_level;

        let mut enc = ENCODER.borrow(cs).borrow_mut();
        let prev_ab = enc.prev_ab;
        enc.prev_ab = curr_ab;

        let a_rose = (prev_ab & 0b10 == 0) && (curr_ab & 0b10 != 0);
        let b_rose = (prev_ab & 0b01 == 0) && (curr_ab & 0b01 != 0);

        if b_rose { enc.last_b_rise = Some(now); }
        if !a_rose { return; }

        if b_rose {
            enc.errors += 1;
            enc.last_a_rise = Some(now);
            return;
        }

        let too_fast = enc.last_a_rise
            .map(|p| (now - p).as_micros() < MIN_STEP_US)
            .unwrap_or(false);
        if too_fast { return; }

        if let Some(prev_a) = enc.last_a_rise {
            let period_us = (now - prev_a).as_micros();
            let head = enc.period_head;
            if enc.period_count == 8 {
                enc.period_sum = enc.period_sum.saturating_sub(enc.periods[head]);
            }
            enc.periods[head] = period_us;
            enc.period_sum = enc.period_sum.saturating_add(period_us);
            enc.period_head = (head + 1) % 8;
            if enc.period_count < 8 { enc.period_count += 1; }
        }
        enc.last_a_rise = Some(now);

        let last_b = enc.last_b_rise;
        let avg_period_opt = if enc.period_count > 0 {
            Some(enc.period_sum / enc.period_count as u64)
        } else {
            None
        };

        let dir_opt = last_b.zip(avg_period_opt).and_then(|(b_time, avg_period)| {
            let since_b = (now - b_time).as_micros();
            if since_b > avg_period + avg_period / 2 { return None; }
            Some(if since_b < avg_period / 2 { 1i8 } else { -1i8 })
        });

        if let Some(dir) = dir_opt {
            if dir == enc.consecutive_dir {
                enc.consecutive_count = enc.consecutive_count.saturating_add(1);
            } else {
                enc.consecutive_dir = dir;
                enc.consecutive_count = 1;
            }
            if enc.consecutive_count >= DIR_CONFIRM_STEPS {
                enc.resolved_dir = enc.consecutive_dir;
            }
            *PENDING_STEP.borrow(cs).borrow_mut() = Some(StepEvent { step: dir });
        }

        if DEBUG_STEPS {
            let since_b_us = enc.last_b_rise
                .map(|t| (now - t).as_micros())
                .unwrap_or(u64::MAX);
            println!("a_rise since_b={}us dir={:+}", since_b_us, enc.resolved_dir);
        }
    });
}

#[main]
fn main() -> ! {
    let peripherals = esp_hal::init(esp_hal::Config::default());
    println!("swimon pulley-detect starting");

    let mut led = Output::new(peripherals.GPIO6, Level::Low, OutputConfig::default());
    led.set_high();
    let t = Instant::now();
    while t.elapsed() < Duration::from_millis(200) {}
    led.set_low();

    let mut pin_a = Input::new(peripherals.GPIO2, InputConfig::default());
    let mut pin_b = Input::new(peripherals.GPIO3, InputConfig::default());

    pin_a.listen(Event::AnyEdge);
    pin_b.listen(Event::AnyEdge);

    critical_section::with(|cs| {
        *PIN_A.borrow(cs).borrow_mut() = Some(pin_a);
        *PIN_B.borrow(cs).borrow_mut() = Some(pin_b);
    });

    unsafe { interrupt::bind_interrupt(Interrupt::GPIO, gpio_interrupt.handler()); }
    interrupt::enable(Interrupt::GPIO, Priority::Priority1).unwrap();

    println!("interrupts enabled — waiting for pulley rotation");
    println!(">rpm:0.00");
    println!(">dir:STOP");

    let mut last_print = Instant::now();
    let print_interval = Duration::from_millis(100);

    loop {
        let _step = critical_section::with(|cs| PENDING_STEP.borrow(cs).borrow_mut().take());

        if last_print.elapsed() >= print_interval {
            last_print = Instant::now();

            let (rpm_x100, dir, errors, last_a_rise) = critical_section::with(|cs| {
                let enc = ENCODER.borrow(cs).borrow();
                (enc.rpm_x100(), enc.direction(), enc.errors, enc.last_a_rise)
            });

            let stalled = last_a_rise
                .map(|t| t.elapsed().as_micros() > STALL_TIMEOUT_US)
                .unwrap_or(true);

            if stalled {
                println!(">rpm:0.00");
                println!(">dir:STOP");
            } else {
                println!(">rpm:{}.{:02}", rpm_x100 / 100, rpm_x100 % 100);
                let dir_str = match dir { 1 => "CW", -1 => "CCW", _ => "UNK" };
                println!(">dir:{}", dir_str);
            }

            if errors > 0 { println!(">errors:{}", errors); }
        }
    }
}
