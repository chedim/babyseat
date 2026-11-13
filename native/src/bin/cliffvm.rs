//! cliffvm: the field VM as a standalone app, no Python (docs/cliffvm.md). main() parses arguments and runs the pack folder.
use std::sync::atomic::{AtomicBool, Ordering};

use membrane_vm::cli::{self, Cmd};

static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn on_term(_: libc::c_int) { STOP.store(true, Ordering::SeqCst); }

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match cli::parse(&args) {
        Ok(Cmd::Version) => println!("{} {}", env!("CARGO_BIN_NAME"), env!("CARGO_PKG_VERSION")),
        Ok(Cmd::Help) => println!("{}", cli::USAGE),
        Ok(Cmd::Run(r)) => {
            unsafe {
                libc::signal(libc::SIGTERM, on_term as *const () as usize);
                libc::signal(libc::SIGINT, on_term as *const () as usize);
            }
            std::process::exit(cli::run(&r, &STOP));
        }
        Err(e) => { eprintln!("cliffvm: {e}\n{}", cli::USAGE); std::process::exit(64); }
    }
}
