//! The graph link, slice 2, the SEAT SIDE (#1143, docs/graph-link.md "Slice 2: built"): `POST /ui/link`, the owner side of the subscriptions, on the shipped barest seat with a FAKE CLIENT that posts
//! frames (the page's engine is not involved). Counts and content, never time: frames, rows, requests. The old header follows.
//! barest seat (no real `claude`, an ephemeral port): `GET /ui/fieldvm` names the page (`page`: `p` + 8 hex digits, one per load, and the entity `Link:page` the page's packs read),
//! the classes `Vm` (stored, one row per page, swept when the seat starts) and `Page` (virtual, held by its `Vm`) take the page's writes through the ordinary door (`POST /mcp`
//! `entity_write`), the SEAT stamps `Vm.seen`, `Vm.state` is `connected` or `stale` by the fact `link_stale_s`, and `Help:page` tells a model how to read it all.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::Duration;

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
struct Run { child: Child, _lines: Receiver<String>, port: u16 }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); } }

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-active-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
/// a start on a store another seat has just left can meet its lock for a moment ("database is locked"): try again
fn start(dir: &std::path::Path, facts: &[&str]) -> Run {
    for _ in 0..4 { if let Some(r) = try_start(dir, facts) { return r; } std::thread::sleep(Duration::from_millis(500)); }
    try_start(dir, facts).unwrap_or_else(|| panic!("the door came up: {}", std::fs::read_to_string(dir.join("err.log")).unwrap_or_default()))
}
fn try_start(dir: &std::path::Path, facts: &[&str]) -> Option<Run> {
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    let packs: Vec<String> = list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(|n| r.join(format!("membrane/packs/{n}.c1f")).to_string_lossy().to_string()).collect();
    let mut cmd = Command::new(BIN);
    cmd.current_dir(dir).arg("run").args(packs).arg(r.join("membrane/apps/barest.c1f")).args(["--store", dir.join("seat.sqlite").to_str().unwrap(), "--port", "0"]);
    for f in facts { cmd.arg("--fact").arg(f); }
    let mut child = cmd.stdout(Stdio::piped()).stderr(std::fs::File::create(dir.join("err.log")).unwrap()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let line = rx.recv_timeout(Duration::from_secs(120)).ok()?;
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    Some(Run { child, _lines: rx, port })
}
fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(30))).unwrap();
    s.write_all(format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).unwrap();
    let mut out = vec![]; let _ = s.read_to_end(&mut out);
    let text = String::from_utf8_lossy(&out).to_string();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    (head.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0), body.to_string())
}
fn call(port: u16, tool: &str, args: J) -> String {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string()
}

fn snap(port: u16) -> J { let (s, b) = http(port, "GET", "/ui/fieldvm", ""); assert_eq!(s, 200, "{b}"); serde_json::from_str(&b).unwrap() }
fn is_ns(s: &str) -> bool { s.len() == 9 && s.starts_with('p') && s[1..].bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) }
fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})) }
fn caps() -> J { json!({"protocol": 1, "encodings": ["json"], "provides": [{"pack": "dom", "major": 1}], "limits": {"subscriptions": "none yet"}}) }
fn report() -> J { json!({"view": "chat", "hidden": false, "load_errors": {}, "packs": {"shell_view": 1234}}) }
fn tell(port: u16, ns: &str) {
    call(port, "entity_write", json!({"spec": format!("Vm:{ns}"), "prop": "caps", "value": caps()}));
    call(port, "entity_write", json!({"spec": format!("{ns}::Page:main"), "prop": "report", "value": report()}));
}


fn frame(port: u16, ns: &str, n: u64, ack: u64, subs: &[(&str, &str)], unsubs: &[(&str, &str)], hidden: bool) -> (u16, J) {
    let body = json!({"v": 1, "ns": ns, "n": n, "ack": ack, "hidden": hidden, "subs": subs.iter().map(|(e, p)| json!([e, p])).collect::<Vec<_>>(), "unsubs": unsubs.iter().map(|(e, p)| json!([e, p])).collect::<Vec<_>>()});
    let (s, b) = http(port, "POST", "/ui/link", &body.to_string());
    (s, serde_json::from_str(&b).unwrap_or(J::Null))
}
fn vals(r: &J) -> Vec<J> { r["vals"].as_array().cloned().unwrap_or_default() }
fn set(port: u16, id: &str, v: J) { call(port, "entity_write", json!({"spec": format!("Setting:{id}"), "prop": "set", "value": v})); }
fn row<'a>(r: &'a [J], id: &str, prop: &str) -> Option<&'a J> { r.iter().find(|x| x["id"] == id && x["prop"] == prop) }
const STEPS: &str = "membrane::Setting:chat.max_steps";

#[test]
fn a_subscribed_setting_arrives_in_one_reply_and_a_tokenless_shape_is_a_400() {
    let run = start(&tmp("a"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (s, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current"), (STEPS, "label")], &[], false);
    assert_eq!(s, 200, "{r}");
    let v = vals(&r);
    assert_eq!(v.len(), 2, "one reply, both values: {r}");
    assert_eq!(row(&v, STEPS, "current").unwrap()["value"], json!(2048), "{r}");
    assert_eq!(row(&v, STEPS, "label").unwrap()["value"], json!("Most steps per answer"));
    assert_eq!(r["n"], json!(1), "the reply is the seat's frame 1");
    assert!(r["epoch"].is_string() || r["epoch"].is_number(), "the boot epoch is in every reply: {r}");
    let (s, _) = http(run.port, "POST", "/ui/link", "{\"v\":1}");
    assert_eq!(s, 400, "a frame with no name and no counter is refused");
    let (s, _) = http(run.port, "GET", "/ui/link", "");
    assert_eq!(s, 405);
}

#[test]
fn policy_chat_is_refused_with_a_reason_setting_is_allowed_and_the_list_is_data() {
    let run = start(&tmp("p"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[("membrane::Chat:main", "title"), ("Chat:main", "name"), ("membrane::Boot:seat", "door_secret_file"), (STEPS, "current"), ("p00000000::Shell:main", "open")], &[], false);
    let v = vals(&r);
    let why = |id: &str| v.iter().find(|x| x["id"] == id).map(|x| x["refused"].as_str().unwrap_or("").to_string());
    assert!(why("membrane::Chat:main").unwrap().starts_with("refused: ") && why("membrane::Chat:main").unwrap().contains("chat"), "{r}");
    assert!(why("Chat:main").unwrap().starts_with("refused: "), "the bare spelling too: {r}");
    assert!(why("membrane::Boot:seat").unwrap().contains("allow-list"), "empty by default: nothing but Setting: is named: {r}");
    assert!(why("p00000000::Shell:main").unwrap().starts_with("refused: "), "another namespace is not the seat's: {r}");
    assert_eq!(row(&v, STEPS, "current").unwrap()["value"], json!(2048));
    // the allow-list is an entity the operator or model writes: add Boot: and it is allowed; add Chat: and it is STILL refused
    call(run.port, "entity_write", json!({"spec": "LinkAllow:more", "prop": "prefixes", "value": ["Boot:", "Chat:"]}));
    let (_, r) = frame(run.port, &ns, 2, 1, &[("membrane::Chat:main", "other")], &[], false);
    assert!(vals(&r).iter().any(|x| x["id"] == "membrane::Chat:main" && x["refused"].as_str().unwrap_or("").contains("never")), "Chat: is never allowed, whatever the list says: {r}");
    let (_, r) = frame(run.port, &ns, 3, 2, &[("membrane::Boot:seat", "seat_name")], &[], false);
    assert!(vals(&r).iter().any(|x| x["id"] == "membrane::Boot:seat" && x["prop"] == "seat_name"), "an allowed prefix is sent: {r}");
}

#[test]
fn a_value_bigger_than_the_most_is_refused_with_its_size() {
    let run = start(&tmp("b"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    call(run.port, "entity_write", json!({"spec": "Setting:test.big", "prop": "value", "value": "x".repeat(70_000)}));
    call(run.port, "entity_write", json!({"spec": "Setting:test.small", "prop": "value", "value": "y".repeat(100)}));
    let (_, r) = frame(run.port, &ns, 1, 0, &[("membrane::Setting:test.big", "current"), ("membrane::Setting:test.small", "current")], &[], false);
    let v = vals(&r);
    let big = v.iter().find(|x| x["id"] == "membrane::Setting:test.big").unwrap_or_else(|| panic!("{r}"));
    assert!(big["refused"].as_str().unwrap().contains("too large") && big["refused"].as_str().unwrap().contains("65536"), "{big}");
    assert_eq!(row(&v, "membrane::Setting:test.small", "current").unwrap()["value"].as_str().unwrap().len(), 100);
    assert!(r.to_string().len() < 2000, "the 70 KB value was never sent: {} bytes", r.to_string().len());
}

#[test]
fn coalescing_many_changes_between_two_polls_is_one_value_the_last() {
    let run = start(&tmp("c"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current")], &[], false);
    assert_eq!(vals(&r).len(), 1);
    for i in 0..20 { set(run.port, "chat.max_steps", json!(100 + i)); }
    let (_, r) = frame(run.port, &ns, 2, 1, &[], &[], false);
    let v = vals(&r);
    assert_eq!(v.len(), 1, "20 writes, ONE pending value per (link, node): {r}");
    assert_eq!(v[0]["value"], json!(119), "the last wins");
    assert_eq!(r["n"], json!(2));
    // acked, nothing changed: a poll with no news is empty and does not count as a new frame of the seat
    let (_, r) = frame(run.port, &ns, 3, 2, &[], &[], false);
    assert_eq!(vals(&r).len(), 0, "{r}");
    assert_eq!(r["n"], json!(2), "an empty reply is not a numbered frame");
}

#[test]
fn thirty_subscriptions_are_one_frame_out_and_one_frame_back() {
    let run = start(&tmp("t30"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let ids: Vec<String> = ["model.chat", "model.default", "chat.max_steps", "chat.timeout", "access.owner", "access.port", "limits.effect_workers", "limits.warm_rows", "import.page", "home.allow"].iter().map(|k| format!("membrane::Setting:{k}")).collect();
    let mut subs: Vec<(&str, &str)> = vec![]; for i in &ids { for p in ["current", "label", "kind"] { subs.push((i.as_str(), p)); } }
    assert_eq!(subs.len(), 30);
    let (s, r) = frame(run.port, &ns, 1, 0, &subs, &[], false);
    assert_eq!(s, 200);
    assert_eq!(vals(&r).len(), 30, "{r}");
    let bytes = r.to_string().len();
    assert!(bytes < 6000, "30 small values are {bytes} bytes");
}

#[test]
fn the_caps_two_hundred_subscriptions_a_link_and_sixteen_links() {
    let run = start(&tmp("k"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    for i in 0..205 { call(run.port, "entity_write", json!({"spec": format!("Setting:t.k{i:03}"), "prop": "value", "value": i})); }
    let ids: Vec<String> = (0..205).map(|i| format!("membrane::Setting:t.k{i:03}")).collect();
    let subs: Vec<(&str, &str)> = ids.iter().map(|i| (i.as_str(), "current")).collect();
    let (s, r) = frame(run.port, &ns, 1, 0, &subs, &[], false);
    assert_eq!(s, 200);
    let v = vals(&r);
    let refused: Vec<&J> = v.iter().filter(|x| x["refused"].is_string()).collect();
    assert_eq!(v.len() - refused.len(), 200, "200 subscriptions are served");
    assert_eq!(refused.len(), 5, "the rest are refused, not queued");
    assert!(refused[0]["refused"].as_str().unwrap().contains("too many subscriptions"), "{}", refused[0]);
    // the 17th link
    let mut others = vec![];
    for _ in 0..15 { let o = snap(run.port)["page"].as_str().unwrap().to_string(); let (s, _) = frame(run.port, &o, 1, 0, &[], &[], false); assert_eq!(s, 200); others.push(o); }
    let last = snap(run.port)["page"].as_str().unwrap().to_string();
    let (s, r) = frame(run.port, &last, 1, 0, &[(STEPS, "current")], &[], false);
    assert_eq!(s, 200, "{r}");
    assert!(r["refused"].as_str().unwrap_or("").contains("too many links"), "the 17th link is refused with a reason: {r}");
    assert_eq!(vals(&r).len(), 0);
}

#[test]
fn an_unacked_window_of_eight_frames_is_a_resync_snapshot_not_a_queue() {
    let run = start(&tmp("w"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current"), ("membrane::Setting:chat.timeout", "current")], &[], false);
    assert_eq!(vals(&r).len(), 2);
    // the client never acks: each change is one more unacked frame of the seat
    let mut last = r;
    for i in 0..9u64 {
        set(run.port, "chat.max_steps", json!(500 + i));
        let (_, r) = frame(run.port, &ns, 2 + i, 0, &[], &[], false);
        last = r;
    }
    assert_eq!(last["resync"], json!(true), "past 8 unacked frames the owner stops deltas and sends a snapshot: {last}");
    assert_eq!(vals(&last).len(), 2, "the snapshot is every subscribed node, once: {last}");
    // the client takes it (acks its frame) and is back on deltas
    let n = last["n"].as_u64().unwrap();
    let (_, r) = frame(run.port, &ns, 20, n, &[], &[], false);
    assert_eq!(vals(&r).len(), 0, "{r}");
    assert_ne!(r["resync"], json!(true));
}

#[test]
fn a_lost_reply_is_sent_again_and_a_duplicate_frame_is_dropped_by_its_counter() {
    let run = start(&tmp("l"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current")], &[], false);
    assert_eq!(vals(&r).len(), 1);
    // the client says it saw nothing (ack 0): the value comes again
    let (_, r) = frame(run.port, &ns, 2, 0, &[], &[], false);
    assert_eq!(vals(&r).len(), 1, "unacked, so it is still owed: {r}");
    // a retried POST of frame 2 is a duplicate: answered as one, applies nothing
    let (_, d) = frame(run.port, &ns, 2, 0, &[], &[], false);
    assert_eq!(d["dup"], json!(true), "{d}");
    assert_eq!(vals(&d).len(), 0);
}

#[test]
fn unsubscribing_stops_the_values_and_a_hidden_page_is_sent_none_until_it_comes_back() {
    let run = start(&tmp("h2"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current"), ("membrane::Setting:chat.timeout", "current")], &[], false);
    assert_eq!(vals(&r).len(), 2);
    set(run.port, "chat.max_steps", json!(7)); set(run.port, "chat.timeout", json!(9));
    let (_, r) = frame(run.port, &ns, 2, 1, &[], &[("membrane::Setting:chat.timeout", "current")], false);
    let v = vals(&r);
    assert_eq!(v.len(), 1, "the unsubscribed one is not sent: {r}");
    assert_eq!(v[0]["value"], json!(7));
    // hidden: the owner keeps the revisions and sends no value
    let (_, r) = frame(run.port, &ns, 3, 2, &[], &[], true);
    assert_eq!(vals(&r).len(), 0);
    set(run.port, "chat.max_steps", json!(8));
    let (_, r) = frame(run.port, &ns, 4, 2, &[], &[], true);
    assert_eq!(vals(&r).len(), 0, "a hidden page gets nothing: {r}");
    let (_, r) = frame(run.port, &ns, 5, 2, &[], &[], false);
    assert_eq!(vals(&r).len(), 1, "visible again: what changed meanwhile arrives (the page's ack says what it holds): {r}");
    assert_eq!(vals(&r)[0]["value"], json!(8));
    // a re-subscription of the dropped one sends its CURRENT value again
    let (_, r) = frame(run.port, &ns, 6, r["n"].as_u64().unwrap(), &[("membrane::Setting:chat.timeout", "current")], &[], false);
    assert_eq!(vals(&r).len(), 1);
    assert_eq!(vals(&r)[0]["value"], json!(9));
}

#[test]
fn an_empty_poll_is_held_until_something_changes_and_a_newer_frame_releases_it() {
    let run = start(&tmp("hold"), &["link_hold_s=20"]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current")], &[], false);
    assert_eq!(vals(&r).len(), 1);
    let port = run.port; let ns2 = ns.clone();
    let poll = std::thread::spawn(move || frame(port, &ns2, 2, 1, &[], &[], false));
    std::thread::sleep(Duration::from_millis(1500));
    assert!(!poll.is_finished(), "nothing changed: the poll is held, not answered");
    set(run.port, "chat.max_steps", json!(31));
    let (s, r) = poll.join().unwrap();
    assert_eq!(s, 200);
    assert_eq!(vals(&r).len(), 1, "the change releases it with the value: {r}");
    assert_eq!(vals(&r)[0]["value"], json!(31));
    // a held poll is released EMPTY by a newer frame of the same page
    let (port, ns3, n) = (run.port, ns.clone(), r["n"].as_u64().unwrap());
    let poll = std::thread::spawn(move || frame(port, &ns3, 3, n, &[], &[], false));
    std::thread::sleep(Duration::from_millis(1000));
    assert!(!poll.is_finished());
    let (_, r) = frame(run.port, &ns, 4, n, &[], &[("membrane::Setting:chat.max_steps", "current")], false);
    assert_eq!(vals(&r).len(), 0, "{r}");
    let (_, old) = poll.join().unwrap();
    assert_eq!(old["superseded"], json!(true), "{old}");
}

#[test]
fn a_poll_that_nothing_answers_is_empty_after_the_hold_and_costs_one_frame() {
    let run = start(&tmp("hold2"), &["link_hold_s=1"]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current")], &[], false);
    assert_eq!(vals(&r).len(), 1);
    let (s, r) = frame(run.port, &ns, 2, 1, &[], &[], false);
    assert_eq!(s, 200);
    assert_eq!(vals(&r).len(), 0, "{r}");
    assert!(r.to_string().len() < 200, "the keepalive is small: {} bytes", r.to_string().len());
}

#[test]
fn a_page_that_talks_is_connected_and_the_lease_decides_stale() {
    let run = start(&tmp("lease"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, _) = frame(run.port, &ns, 1, 0, &[], &[], false);
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "state"), "connected", "a frame stamps the page as heard (no caps, no report)");
    let (_, _) = frame(run.port, &ns, 2, 0, &[], &[], true);
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "hidden"), "True", "the seat knows the page is hidden from its frames");
    drop(run);
    let run = start(&tmp("lease0"), &["link_lease_s=0"]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let _ = frame(run.port, &ns, 1, 0, &[], &[], false);
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "state"), "stale", "a visible page silent past link_lease_s is stale");
    drop(run);
    let run = start(&tmp("lease1"), &["link_lease_s=0", "link_lease_hidden_s=600"]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let _ = frame(run.port, &ns, 1, 0, &[], &[], true);
    std::thread::sleep(Duration::from_millis(1500));
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "state"), "connected", "a hidden page has the long lease");
}

#[test]
fn a_frame_over_the_most_is_413_and_an_expired_link_forgets_its_subscriptions() {
    let run = start(&tmp("big"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let pad = "x".repeat(270_000);
    let (s, _) = http(run.port, "POST", "/ui/link", &json!({"v": 1, "ns": ns, "n": 1, "ack": 0, "pad": pad}).to_string());
    assert_eq!(s, 413, "max_frame is 256 KB");
    drop(run);
    // a link silent past its lease is gone: its row and every subscription row are retired (nothing is kept for a page that left)
    let run = start(&tmp("gone"), &["link_lease_s=0"]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current"), ("membrane::Setting:chat.timeout", "current")], &[], false);
    assert_eq!(vals(&r).len(), 2);
    let mut left = String::new();
    for _ in 0..30 { left = call(run.port, "run_cliff", json!({"code": "len(instances('LinkPeer'))"})); if left == "0" { break; } std::thread::sleep(Duration::from_millis(200)); }
    assert_eq!(left, "0", "the link row and its two subscription rows are collected");
    assert_eq!(read(run.port, &format!("Vm:{ns}"), "state"), "stale");
}

#[test]
fn the_chat_doorbell_rings_when_a_message_is_posted_and_is_not_under_chat() {
    let run = start(&tmp("bell"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[("membrane::Doorbell:chat", "at")], &[], false);
    let v = vals(&r);
    assert!(v.iter().any(|x| x["id"] == "membrane::Doorbell:chat" && x["prop"] == "at"), "the doorbell is subscribable (it is not under Chat:): {r}");
    let before = vals(&r)[0]["value"].clone();
    let (s, _) = http(run.port, "POST", "/membrane/chat/message", &json!({"text": "hello"}).to_string());
    assert_eq!(s, 202);
    let (_, r) = frame(run.port, &ns, 2, 1, &[], &[], false);
    let v = vals(&r);
    assert_eq!(v.len(), 1, "a message arriving is one change of the doorbell: {r}");
    assert_ne!(v[0]["value"], before);
}

#[test]
fn a_page_the_seat_does_not_know_with_a_late_counter_is_told_to_resubscribe() {
    let run = start(&tmp("reset"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &ns, 1, 0, &[(STEPS, "current")], &[], false);
    assert_ne!(r["reset"], json!(true), "a first frame is not a reset: {r}");
    // a seat that restarted (or a link that expired) meets a page at frame 57: it knows nothing of its subscriptions
    let other = snap(run.port)["page"].as_str().unwrap().to_string();
    let (_, r) = frame(run.port, &other, 57, 40, &[], &[], false);
    assert_eq!(r["reset"], json!(true), "the page must send every subscription again: {r}");
    assert_eq!(vals(&r).len(), 0);
    // the full set comes in the next frame and is served as from a new page
    let (_, r) = frame(run.port, &other, 58, 0, &[(STEPS, "current")], &[], false);
    assert_ne!(r["reset"], json!(true), "{r}");
    assert_eq!(vals(&r).len(), 1);
}

#[test]
fn a_reply_never_passes_the_frame_limit_the_rest_follows_in_the_next_replies() {
    let run = start(&tmp("split"), &[]);
    let ns = snap(run.port)["page"].as_str().unwrap().to_string();
    for i in 0..6 { call(run.port, "entity_write", json!({"spec": format!("Setting:test.h{i}"), "prop": "value", "value": "z".repeat(60_000)})); }
    let ids: Vec<String> = (0..6).map(|i| format!("membrane::Setting:test.h{i}")).collect();
    let subs: Vec<(&str, &str)> = ids.iter().map(|i| (i.as_str(), "current")).collect();
    let (_, r) = frame(run.port, &ns, 1, 0, &subs, &[], false);
    let mut got = vals(&r).len();
    assert!(r.to_string().len() <= 262_144, "the first reply is within the limit: {} bytes", r.to_string().len());
    assert!(got > 0 && got < 6, "split by values: {got} of 6 in the first reply");
    let (mut n, mut ack) = (2u64, r["n"].as_u64().unwrap());
    for _ in 0..4 {
        if got == 6 { break; }
        let (_, r) = frame(run.port, &ns, n, ack, &[], &[], false);
        assert!(r.to_string().len() <= 262_144, "every reply is within the limit");
        got += vals(&r).len(); ack = r["n"].as_u64().unwrap(); n += 1;
    }
    assert_eq!(got, 6, "all six values arrived, none twice");
}
