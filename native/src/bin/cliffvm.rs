//! cliffvm: the field VM as a standalone app, no Python (docs/cliffvm.md). main() parses arguments and runs the pack folder.
use std::sync::atomic::{AtomicBool, Ordering};

use membrane_vm::cli::{self, Cmd};

#[cfg(feature = "alloc-stats")]
#[global_allocator]
static ALLOC: membrane_vm::alloc_stats::Counting = membrane_vm::alloc_stats::Counting;

static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn on_term(_: libc::c_int) { STOP.store(true, Ordering::SeqCst); }

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match cli::parse(&args) {
        Ok(Cmd::Version) => println!("{} {}", env!("CARGO_BIN_NAME"), env!("CARGO_PKG_VERSION")),
        Ok(Cmd::Help) => println!("{}", cli::USAGE),
        Ok(Cmd::Mint(m)) => {
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
            match cli::mint(&m, now) {
                Ok(t) => println!("{t}"),
                Err(e) => { eprintln!("cliffvm: token mint: {e}"); std::process::exit(2); }
            }
        }
        Ok(Cmd::Run(r)) => {
            #[cfg(feature = "alloc-stats")]
            membrane_vm::alloc_stats::report();
            unsafe {
                libc::signal(libc::SIGTERM, on_term as *const () as usize);
                libc::signal(libc::SIGINT, on_term as *const () as usize);
            }
            std::process::exit(cli::run(&r, &STOP));
        }
        Err(e) => { eprintln!("cliffvm: {e}\n{}", cli::USAGE); std::process::exit(64); }
    }
}
