//! The local calendar for `.c1f` (`localtime(ts)`): the OS's own time zone rules (localtime_r / strftime on unix),
//! so "yesterday", "this morning" and the NOW line are the operator's local ones. Elsewhere (the browser build): UTC.
use crate::expr::V;

fn civil(days: i64) -> (i64, u32, u32) {
    // days since 1970-01-01 -> (year, month, day), Howard Hinnant's algorithm
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// (offset from UTC in seconds, the zone's abbreviation) at `ts`.
#[cfg(unix)]
fn zone(ts: f64) -> (i64, String) {
    unsafe {
        let t = ts.floor() as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() { return (0, "UTC".into()); }
        let mut buf = [0i8; 32];
        let n = libc::strftime(buf.as_mut_ptr() as *mut _, buf.len(), b"%Z\0".as_ptr() as *const _, &tm);
        let name = std::ffi::CStr::from_ptr(buf.as_ptr() as *const _).to_string_lossy().into_owned();
        (tm.tm_gmtoff as i64, if n == 0 { String::new() } else { name })
    }
}
#[cfg(not(unix))]
fn zone(_ts: f64) -> (i64, String) { (0, "UTC".into()) }

pub fn local(ts: f64) -> V {
    let (off, name) = zone(ts);
    let secs = ts.floor() as i64 + off;
    let days = secs.div_euclid(86400);
    let rem = secs.rem_euclid(86400);
    let (y, m, d) = civil(days);
    let (hh, mm) = (rem / 3600, (rem % 3600) / 60);
    V::Dict(vec![
        ("year".into(), V::Int(y)), ("month".into(), V::Int(m as i64)), ("day".into(), V::Int(d as i64)),
        ("hour".into(), V::Int(hh)), ("minute".into(), V::Int(mm)),
        ("date".into(), V::Str(format!("{:04}-{:02}-{:02}", y, m, d))),
        ("clock".into(), V::Str(format!("{:02}:{:02}", hh, mm))),
        ("zone".into(), V::Str(name)),
        ("day_number".into(), V::Int(days)),
    ])
}

pub const JUST_NOW_S: f64 = 5.0 * 60.0;   // conv_banner's buckets (operator-approved there)
pub const HOURS_S: f64 = 12.0 * 3600.0;
pub const DAYS_MAX: i64 = 7;

/// conv_banner.age / _bucket: "just now", "N min ago", "Nh ago", "this morning"/"this afternoon", "yesterday",
/// "N days ago", else the date (all in the local calendar).
pub fn ago(ts: f64, now: f64) -> String {
    let ts = ts.min(now);
    let dt = (now - ts).max(0.0);
    if dt < JUST_NOW_S { return "just now".into(); }
    if dt < 3600.0 { return format!("{} min ago", (dt / 60.0).floor() as i64); }
    if dt < HOURS_S { return format!("{}h ago", (dt / 3600.0).floor() as i64); }
    let (then, nw) = (local(ts), local(now));
    let get = |v: &V, k: &str| -> i64 { if let V::Dict(d) = v { for (x, y) in d { if x == k { if let V::Int(n) = y { return *n; } } } } 0 };
    let days = get(&nw, "day_number") - get(&then, "day_number");
    if days <= 0 { return if get(&then, "hour") < 12 { "this morning".into() } else { "this afternoon".into() }; }
    if days == 1 { return "yesterday".into(); }
    if days < DAYS_MAX { return format!("{} days ago", days); }
    if let V::Dict(d) = then { for (k, v) in d { if k == "date" { if let V::Str(s) = v { return s; } } } }
    String::new()
}
