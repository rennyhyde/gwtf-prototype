// #![no_std]
// #![no_main]
// #![deny(
//     clippy::mem_forget,
//     reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
//     holding buffers for the duration of a data transfer."
// )]
// #![deny(clippy::large_stack_frames)]

// use esp_hal::clock::CpuClock;
// use esp_hal::main;
// use esp_hal::time::{Duration, Instant};
// use rtt_target::rprintln;

// #[panic_handler]
// fn panic(_: &core::panic::PanicInfo) -> ! {
//     loop {}
// }

// // This creates a default app-descriptor required by the esp-idf bootloader.
// // For more information see: <https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/app_image_format.html#application-description>
// esp_bootloader_esp_idf::esp_app_desc!();

// #[allow(
//     clippy::large_stack_frames,
//     reason = "it's not unusual to allocate larger buffers etc. in main"
// )]
// #[main]
// fn main() -> ! {
//     // generator version: 1.1.0

//     rtt_target::rtt_init_print!();

//     let config = esp_hal::Config::default().with_cpu_clock(CpuClock::max());
//     let _peripherals = esp_hal::init(config);

//     loop {
//         rprintln!("Hello world!");
//         let delay_start = Instant::now();
//         while delay_start.elapsed() < Duration::from_millis(500) {}
//     }

//     // for inspiration have a look at the examples at https://github.com/esp-rs/esp-hal/tree/esp-hal-v~1.0/examples
// }

/*
Can confirm this is working! Yayyyy
*/
#![no_std]
#![no_main]
#![deny(
    clippy::mem_forget,
    reason = "mem::forget is generally not safe to do with esp_hal types, especially those \
    holding buffers for the duration of a data transfer."
)]

// use esp_backtrace as _;
use esp_hal::{
    // delay::Delay,
    gpio::{Level, Output, OutputConfig},
    main,
};
use esp_hal::time::{Duration, Instant};
use defmt;


// use esp_println::println;
esp_bootloader_esp_idf::esp_app_desc!();

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

#[main]
fn main() -> ! {
    let peripherals = esp_hal::init(esp_hal::Config::default());

    // println!("Hello World");
    defmt::debug!("Hello World");

    let mut led = Output::new(peripherals.GPIO6, Level::Low, OutputConfig::default());
    // let delay = Delay::new();

    loop {
        led.set_high();
        // println!("LED HIGH");
        defmt::debug!("LED HIGH");
        let delay_start = Instant::now();
        while delay_start.elapsed() < Duration::from_millis(500) {}
        led.set_low();
        defmt::debug!("LED HIGH");
        let delay_start = Instant::now();
        while delay_start.elapsed() < Duration::from_millis(500) {}
        // delay.delay_millis(1000);
        
        // delay.delay_millis(1000);
    }
}
