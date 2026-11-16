//! Deadlines shared by the seat-spawning test files (#1143). A deadline here only bounds a HANG: every wait polls its condition and returns the moment it holds, so a
//! passing test costs the same whatever number is chosen here. The numbers are sized for a machine under heavy load (all cores busy plus a second heavy job: the boot of
//! a seat took 30+ s there, a turn of a fake CLI more than 60 s), not for the idle case. They are TEST deadlines, not product constants.
#![allow(dead_code)]
use std::time::Duration;

/// from spawning a seat to the first line of its stdout (the door's address): the boot reads, loads and starts every pack
pub const DOOR_UP: Duration = Duration::from_secs(180);
/// the default deadline of a poll for a condition (a turn done, a row written, a receipt landed)
pub const WAIT: Duration = Duration::from_secs(180);
/// a socket read: one HTTP answer from the seat's door (a route that runs a turn's setup, a command, a query)
pub const IO: Duration = Duration::from_secs(120);

/// poll `f` until it gives Some; `what` names the condition in the panic of a hang
pub fn wait_for<T>(what: &str, deadline: Duration, mut f: impl FnMut() -> Option<T>) -> T {
    let t = std::time::Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < deadline, "timed out after {deadline:?} waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}
