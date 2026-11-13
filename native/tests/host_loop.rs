//! The host loop in Rust (src/host.rs): stamps, the journal of OUTSIDE writes, and replay = the same state.
use membrane_vm::expr;
use membrane_vm::host::{self, is_outside, stamp_born_writer, stamp_writer, Host, Line, Options};
use serde_json::{json, Map, Value as J};
use std::cell::Cell;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const MS: u64 = 1_000_000;
thread_local! { static NOW: Cell<u64> = const { Cell::new(0) }; static STEP: Cell<u64> = const { Cell::new(0) }; }
fn fake() -> u64 { NOW.with(|n| { let v = n.get(); n.set(v + STEP.with(|s| s.get())); v }) }
fn use_clock(step_ms: u64) { NOW.with(|n| n.set(0)); STEP.with(|s| s.set(step_ms * MS)); expr::set_clock(Some(fake)); }

const WEB: &str = "PACK web 1
Request.reply getter:
  WHEN this.path == '/missing': {'status': 404, 'body': 'no ' + this.path}
  ELSE: {'status': 200, 'body': 'hello ' + this.path + ' ' + str(this.authed)}
Request.reply on-setter answer:
  ALWAYS:
    this.respond = this.reply
";

fn slow() -> String {
    let items = (0..600).map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
    format!("PACK t 1\nBox.slow getter: this.w + len([x for x in [{items}]])\n")
}

fn tmpfile(tag: &str) -> String {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    std::env::temp_dir().join(format!("hostloop-{tag}-{}-{n}.sqlite3", std::process::id())).to_string_lossy().to_string()
}

fn ticking() -> Box<dyn Fn() -> f64 + Send + Sync> {
    let n = Arc::new(AtomicU64::new(0));
    Box::new(move || 1000.0 + n.fetch_add(1, Ordering::SeqCst) as f64)
}

fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

fn pump_until(h: &Host, mut done: impl FnMut(&Host) -> bool) {
    let wt = h.waiter();
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(15) {
        h.pump();
        if done(h) { return; }
        if let Some(w) = &wt { w.wait(Some(Duration::from_millis(30))); }
    }
    panic!("timed out: {:?}", h.snapshot());
}

fn client(port: u64, path: &str) -> std::sync::mpsc::Receiver<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let raw = format!("GET {path} HTTP/1.1\r\nHost: t\r\n\r\n");
    std::thread::spawn(move || {
        let mut s = TcpStream::connect(("127.0.0.1", port as u16)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(15))).unwrap();
        s.write_all(raw.as_bytes()).unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        tx.send(out).unwrap();
    });
    rx
}

#[test]
fn the_stamping_rules_are_fields_lives() {
    assert_eq!(stamp_writer("answer"), "trigger:field:answer");
    assert_eq!(stamp_writer("field:getter:x"), "trigger:field:getter:x");
    assert_eq!(stamp_writer("field:driver:trip"), "host:driver:trip");
    assert_eq!(stamp_born_writer("Request"), "trigger:field:create");
    assert_eq!(stamp_born_writer("Trip"), "host:driver:trip");
    assert!(is_outside("host:door") && is_outside("person") && !is_outside("trigger:field:answer"));
}

/// what the engine and the rows hold, minus the drivers' receipts (an effect's answer is not re-derivable: replay runs no effects)
fn state(h: &Host) -> (std::collections::BTreeMap<String, J>, std::collections::BTreeMap<String, J>) {
    let skip = |k: &str| k.ends_with("_result") || k.ends_with("_rc");
    let clean = |mut v: J| {
        for key in ["props", "writers"] { if let Some(m) = v.get_mut(key).and_then(|p| p.as_object_mut()) { m.retain(|k, _| !skip(k)); } }
        v
    };
    (h.snapshot().into_iter().map(|(k, v)| (k, clean(v))).collect(), h.rows().into_iter().map(|(k, v)| (k, clean(v))).collect())
}

#[test]
fn a_live_run_is_journalled_outside_only_and_replays_to_the_same_state() {
    use_clock(0);
    let file = tmpfile("live");
    let mut o = Options::new(Some(file.clone()));
    o.plain = true;
    o.clock = ticking();
    let h = Host::open(o).unwrap();
    h.load_pack("web", WEB).unwrap();
    h.load_pack("t", &slow()).unwrap();
    h.rebuild();
    // the boot: the door listens (an outside write: the host's, not a setter's), then a world entity
    h.born("Http:door", "Http", Map::new(), host::BOOT_WRITER);
    h.write("Http:door", "listen", json!({"id": "l1", "port": 0}), host::BOOT_WRITER);
    pump_until(&h, |h| !h.get("Http:door", "listen_result").is_null());
    let port = h.get("Http:door", "listen_result")["port"].as_u64().unwrap();
    h.born("Box:a", "Box", props(json!({"w": 1})), "person");
    // two requests through the door: the pack answers, the host journals the births only
    let (a, b) = (client(port, "/one"), client(port, "/missing"));
    let (mut ra, mut rb) = (None, None);
    pump_until(&h, |_| { if ra.is_none() { ra = a.try_recv().ok(); } if rb.is_none() { rb = b.try_recv().ok(); } ra.is_some() && rb.is_some() });
    let (ra, rb) = (ra.unwrap(), rb.unwrap());
    assert!(ra.starts_with("HTTP/1.1 200") && ra.ends_with("hello /one True"), "{ra}");
    assert!(rb.starts_with("HTTP/1.1 404"), "{rb}");
    // a time kill (injected clock: every read of it is 6 ms): the Trip is an outside write
    use_clock(6);
    h.write("Box:a", "w", json!(2), "person");
    use_clock(0);
    assert_eq!(h.get("Box:a", "slow"), J::Null, "killed: the reasoned null");
    assert_eq!(h.get("Trip:Box:a#slow", "active"), json!(true));
    h.pump();
    let live = state(&h);

    // the journal: outside writes present, engine-derived absent
    let j = h.journal();
    assert!(j.iter().all(|l| is_outside(l.writer())), "an engine-derived write was journalled: {j:?}");
    let has = |f: &dyn Fn(&Line) -> bool| j.iter().any(|l| f(l));
    assert!(has(&|l| matches!(l, Line::Born { eid, writer, .. } if eid == "Http:door" && writer == "host:boot")));
    assert!(has(&|l| matches!(l, Line::Write { eid, prop, .. } if eid == "Http:door" && prop == "listen")));
    assert!(has(&|l| matches!(l, Line::Born { eid, cls, writer, .. } if eid.starts_with("Request:") && cls == "Request" && writer == "host:door")));
    assert_eq!(j.iter().filter(|l| matches!(l, Line::Born { cls, .. } if cls == "Request")).count(), 2);
    assert!(has(&|l| matches!(l, Line::Write { eid, prop, new, writer, .. } if eid == "Box:a" && prop == "w" && new == &json!(2) && writer == "person")));
    assert!(has(&|l| matches!(l, Line::Born { cls, writer, .. } if cls == "Trip" && writer == "host:driver:trip")));
    assert!(has(&|l| matches!(l, Line::Write { eid, prop, writer, .. } if eid.starts_with("Trip:") && prop == "active" && writer == "host:driver:trip")));
    assert!(!has(&|l| matches!(l, Line::Write { prop, .. } if prop == "reply" || prop == "respond" || prop == "slow")), "derived writes are not journalled");
    // the rows carry the stamps
    let rows = h.rows();
    let req = rows.iter().find(|(k, _)| k.starts_with("Request:")).unwrap().1.clone();
    assert_eq!(req["writers"]["reply"], json!("trigger:field:getter:reply"));
    assert_eq!(req["writers"]["respond"], json!("trigger:field:direct:consumed"), "the answer was written by the setter, then the driver consumed the slot");
    assert_eq!(req["writers"]["method"], json!("host:door"));
    assert_eq!(rows["Trip:Box:a#slow"]["writers"]["active"], json!("host:driver:trip"));
    assert_eq!(rows["Box:a"]["writers"]["w"], json!("person"));
    // and the stored journal is the in-memory one (a later process replays it)
    assert_eq!(h.journal_stored().unwrap(), j);

    // REPLAY into a fresh engine and a fresh store: no drivers (a replay redoes no effects), the same packs
    let mut o2 = Options::new(None);
    o2.effects = false;
    let r = Host::open(o2).unwrap();
    r.load_pack("web", WEB).unwrap();
    r.load_pack("t", &slow()).unwrap();
    r.rebuild();
    r.replay(&j);
    let replayed = state(&r);
    assert_eq!(replayed.0, live.0, "the engine's entities");
    assert_eq!(replayed.1, live.1, "the stored rows (values and writers)");
    assert_eq!(r.journal(), j, "the replay's own journal is the same");
    expr::set_clock(None);
    let _ = std::fs::remove_file(&file);
}

#[test]
fn a_pack_entity_written_outside_loads_reloads_refuses_and_replays_to_the_same_packs() {
    use_clock(0);
    let tag = |s: &str| format!("PACK mine 1\nBox.label getter: '{s}' + str(this.w)\n");
    let h = Host::open({ let mut o = Options::new(None); o.clock = ticking(); o }).unwrap();
    h.rebuild();
    h.born("Box:a", "Box", props(json!({"w": 1})), "person");
    assert_eq!(h.get("Box:a", "label"), J::Null, "no pack yet");
    h.born("Pack:mine", "Pack", props(json!({"text": tag("one ")})), "person");
    assert_eq!(h.get("Box:a", "label"), json!("one 1"));
    assert_eq!((h.get("Pack:mine", "loaded"), h.get("Pack:mine", "error")), (json!(true), J::Null));
    h.write("Pack:mine", "text", json!(tag("two ")), "person");                   // hot reload
    assert_eq!(h.get("Box:a", "label"), json!("two 1"));
    h.write("Pack:mine", "text", json!("Box.label getter: ("), "person");        // does not parse: refused, the old pack stays
    assert_eq!(h.get("Box:a", "label"), json!("two 1"));
    assert!(h.get("Pack:mine", "error").as_str().unwrap().starts_with("pack mine"), "{:?}", h.get("Pack:mine", "error"));
    assert_eq!(h.get("Pack:mine", "loaded"), json!(true));
    assert_eq!(h.get("Pack:mine", "good_text"), json!(tag("two ")));
    h.write("Box:a", "w", json!(2), "person");
    assert_eq!(h.get("Box:a", "label"), json!("two 2"));
    // the loaded and error mirrors are derived (never journalled); the texts are the outside writes
    let j = h.journal();
    assert!(j.iter().all(|l| is_outside(l.writer())), "{j:?}");
    assert_eq!(j.iter().filter(|l| matches!(l, Line::Write { prop, .. } if prop == "text")).count(), 2);
    assert!(!j.iter().any(|l| matches!(l, Line::Write { prop, .. } if prop == "error" || prop == "loaded" || prop == "good_text")));
    let live = state(&h);
    let r = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    r.rebuild();
    r.replay(&j);
    assert_eq!(r.get("Box:a", "label"), json!("two 2"));
    let replayed = state(&r);
    assert_eq!(replayed.0, live.0, "the engine's entities (the Pack entity's loaded/error/good_text too)");
    assert_eq!(replayed.1, live.1, "the stored rows");
    // an emptied text removes the pack: its getter is gone and the entity says so
    h.write("Pack:mine", "text", json!(""), "person");
    assert_eq!(h.get("Pack:mine", "loaded"), json!(false));
    assert_eq!(h.get("Pack:mine", "error"), J::Null);
    h.write("Box:a", "w", json!(3), "person");
    assert_eq!(h.get("Box:a", "label"), J::Null, "removed: the getter no longer runs and its stale value is cleared (derived, journal-neutral)");
    // a pack that imports one that arrives later waits (its error says so) and activates when the import arrives
    h.born("Pack:late", "Pack", props(json!({"text": "PACK late 1\nIMPORT base 1\nBox.late getter: 'late'\n"})), "person");
    assert_eq!(h.get("Pack:late", "loaded"), json!(false));
    assert!(h.get("Pack:late", "error").as_str().unwrap().contains("base"), "{:?}", h.get("Pack:late", "error"));
    h.born("Pack:base", "Pack", props(json!({"text": "PACK base 1\nBox.base getter: 'base'\n"})), "person");
    assert_eq!(h.get("Pack:late", "loaded"), json!(true));
    assert_eq!(h.get("Pack:late", "error"), J::Null);
    assert_eq!(h.get("Box:a", "late"), json!("late"));
    expr::set_clock(None);
}

#[test]
fn the_boot_pack_grants_the_tools_pack_at_start_and_a_replay_ends_in_the_same_privilege_state() {
    use_clock(0);
    let boot = std::fs::read_to_string(format!("{}/../membrane/packs/boot.c1f", env!("CARGO_MANIFEST_DIR"))).unwrap();
    let fresh = || { let h = Host::open({ let mut o = Options::new(None); o.clock = ticking(); o.effects = false; o }).unwrap(); let d = std::env::temp_dir().join(format!("hl-boot-{}", std::process::id())); std::fs::create_dir_all(&d).unwrap(); std::fs::write(d.join("boot.c1f"), &boot).unwrap(); h.load_app(&d.join("boot.c1f"), true).unwrap(); h.rebuild(); h };
    let h = fresh();
    assert!(h.pack_privileged("boot"), "the host marks the first app's pack");
    assert!(!h.pack_privileged("door_tools") && !h.pack_privileged("fs"));
    host::boot_started(&h, &Map::new(), true);
    assert!(h.pack_privileged("door_tools"));
    assert!(!h.pack_privileged("fs"), "nothing else is granted");
    assert_eq!(h.get("Pack:door_tools", "privileged"), json!(true));
    assert_eq!(h.get("Pack:door_tools", "granted_by"), json!("pack:boot:grant-door-tools"));
    let j = h.journal();
    // Boot is VIRTUAL (not journalled), so the grant its `started` setter makes is not derivable by a replay: it is journalled as an OUTSIDE write (`host:virtual:<setter>`)
    let grants: Vec<_> = j.iter().filter(|l| matches!(l, Line::Write { eid, .. } if eid.starts_with("Pack:"))).collect();
    assert_eq!(grants.len(), 2, "the tools pack and the unload pack (the barest app's policy): {grants:?}");
    assert!(grants.iter().all(|g| g.writer() == "host:virtual:grant-door-tools"));
    assert!(!j.iter().any(|l| matches!(l, Line::Born { cls, .. } if cls == "Boot")) && !j.iter().any(|l| matches!(l, Line::Write { eid, .. } if eid.starts_with("Boot:"))), "nothing about Boot is journalled: {j:?}");
    let r = fresh();
    r.replay(&j);
    assert!(r.pack_privileged("door_tools"));
    // the replay ends in the same privilege state; what differs is only what is RAM-only (the Boot entity) and who the grant is attributed to (the host's replay of it)
    let (rs, rr) = state(&r);
    let (hs, hr) = state(&h);
    assert_eq!(rs.get("Pack:door_tools").map(|v| &v["props"]["privileged"]), hs.get("Pack:door_tools").map(|v| &v["props"]["privileged"]));
    assert_eq!(rs.keys().filter(|k| !k.starts_with("Boot:")).collect::<Vec<_>>(), hs.keys().filter(|k| !k.starts_with("Boot:")).collect::<Vec<_>>());
    assert_eq!(rr.keys().collect::<Vec<_>>(), hr.keys().collect::<Vec<_>>(), "the same rows are stored");
    assert!(!rr.contains_key("Boot:seat"), "no Boot row");
    assert_eq!(r.get("Pack:door_tools", "granted_by"), json!("host:virtual:grant-door-tools"));
    expr::set_clock(None);
}

#[test]
fn a_setter_retires_an_entity_by_writing_retire_true_and_a_replay_ends_the_same() {
    use_clock(0);
    let pack = "PACK keep 1\nBox.done on-setter bye:\n  WHEN this.done == True:\n    this.retire = True\n";
    let fresh = |store: Option<String>| { let h = Host::open({ let mut o = Options::new(store); o.clock = ticking(); o.effects = false; o }).unwrap(); h.load_pack("keep", pack).unwrap(); h.rebuild(); h };
    let h = fresh(None);
    h.born("Box:a", "Box", props(json!({"w": 1})), "person");
    h.born("Box:b", "Box", props(json!({"w": 2})), "person");
    h.write("Box:a", "done", json!(false), "person");
    assert_eq!(h.get("Box:a", "w"), json!(1), "a false does not retire");
    h.write("Box:a", "done", json!(true), "person");
    assert_eq!(h.get("Box:a", "w"), J::Null, "the engine no longer holds Box:a");
    assert!(!h.snapshot().contains_key("Box:a") && h.snapshot().contains_key("Box:b"));
    assert!(!h.rows().contains_key("Box:a") && h.rows().contains_key("Box:b"), "the stored row is deleted too: {:?}", h.rows().keys().collect::<Vec<_>>());
    let j = h.journal();
    assert!(!j.iter().any(|l| matches!(l, Line::Write { prop, .. } if prop == "retire")), "the retirement is derived, never journalled");
    let r = fresh(None);
    r.replay(&j);
    assert_eq!(state(&r), state(&h), "the replay ends with Box:a gone as well");
    expr::set_clock(None);
}

/// A parent owns its children by the colon path (#1143): a `retire = true` on `A:1` retires `A:1:x` and `A:1:x:y` too, never `A:10` or `A:2`.
#[test]
fn retiring_an_entity_retires_what_its_path_owns_and_replays() {
    let mk = || { let mut o = Options::new(None); o.effects = false; Host::open(o).unwrap() };
    let h = mk();
    h.rebuild();
    for e in ["A:1", "A:1:x", "A:1:x:y", "A:10", "A:2"] { h.born(e, "A", Map::new(), "person"); }
    h.write("A:1", "retire", json!(true), "person");
    let keys: Vec<String> = h.snapshot().keys().cloned().collect();
    assert_eq!(keys, ["A:10", "A:2"], "the engine keeps the others");
    assert_eq!(h.rows().keys().cloned().collect::<Vec<_>>(), ["A:10", "A:2"], "and so does the store");
    let r = mk();
    r.rebuild();
    r.replay(&h.journal());
    assert_eq!(r.snapshot().keys().cloned().collect::<Vec<_>>(), ["A:10", "A:2"]);
}

// ---- a running command's partial output (#1143, the SSE slice) --------------------------------------------------------------------------
const PARTIAL: &str = "PACK pc 1
Chunk.kind getter: 'chunk'
Command.kind getter: 'command'
Command.seen getter: join([x['d'] for x in sort_by([{'n': c.n, 'd': c.data} for c in instances('Chunk') if c.cmd == this.eid], 'n')], '')
";

/// `run = {partial: true}`: each read's complete lines arrive as an outside birth `Chunk:<command>:<n>` ({cmd, id, n, data}) BEFORE the
/// receipt; a run without `partial` makes none; the journal holds the births in order and a replay (no effects) reaches the same state.
#[test]
fn a_command_run_with_partial_delivers_its_lines_as_journalled_chunks_before_the_receipt_and_replays() {
    use_clock(0);
    let dir = std::env::temp_dir().join(format!("hostloop-partial-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
    std::fs::create_dir_all(&dir).unwrap();
    let h = Host::open({ let mut o = Options::new(Some(dir.join("f.sqlite3").to_string_lossy().to_string())); o.clock = ticking(); o }).unwrap();
    h.load_pack("pc", PARTIAL).unwrap();
    h.rebuild();
    let script = "printf 'a\\n'; sleep 0.4; printf 'b\\nc\\n'; sleep 0.4; printf 'tail-without-newline'";
    h.born("Command:p", "Command", props(json!({"cmd": ["sh", "-c", script]})), "person");
    h.born("Command:q", "Command", props(json!({"cmd": ["sh", "-c", "printf 'x\\n'"]})), "person");
    h.write("Command:q", "run", json!({"id": "q", "args": []}), "person");
    h.write("Command:p", "run", json!({"id": "p", "args": [], "partial": true}), "person");
    // the first chunk is in while the run is still going (no receipt yet)
    pump_until(&h, |h| h.get("Command:p", "seen") == json!("a\n"));
    assert_eq!(h.get("Command:p", "run_result"), J::Null, "a chunk lands before the receipt");
    assert_eq!(h.get("Command:p", "seen"), json!("a\n"));
    pump_until(&h, |h| !h.get("Command:p", "run_result").is_null());
    assert_eq!(h.get("Command:p", "seen"), json!("a\nb\nc\ntail-without-newline"), "every byte, once, in order, the last line without its newline too");
    assert_eq!(h.get("Command:p", "run_result")["stdout"], json!("a\nb\nc\ntail-without-newline"), "the receipt still carries the whole output");
    let chunks: Vec<String> = h.snapshot().keys().filter(|k| k.starts_with("Chunk:Command:p:")).cloned().collect();
    assert_eq!(chunks.len(), 3, "one chunk per read with complete lines, the unterminated tail at the end: {chunks:?}");
    assert!(!h.snapshot().keys().any(|k| k.starts_with("Chunk:Command:q:")), "no `partial`, no chunks");
    // the journal: the births, in order, outside (the door's writer); the receipt is not there
    let j = h.journal();
    let born: Vec<(String, String)> = j.iter().filter_map(|l| match l { Line::Born { eid, cls, writer, props, .. } if cls == "Chunk" => { assert_eq!(writer, "host:door"); Some((eid.clone(), props["data"].as_str().unwrap().to_string())) } _ => None }).collect();
    assert_eq!(born.iter().map(|(e, _)| e.as_str()).collect::<Vec<_>>(), ["Chunk:Command:p:1", "Chunk:Command:p:2", "Chunk:Command:p:3"]);
    assert_eq!(born.iter().map(|(_, d)| d.as_str()).collect::<Vec<_>>().concat(), "a\nb\nc\ntail-without-newline");
    let live = state(&h);
    let r = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
    r.load_pack("pc", PARTIAL).unwrap();
    r.rebuild();
    r.replay(&j);
    assert_eq!(r.get("Command:p", "seen"), json!("a\nb\nc\ntail-without-newline"), "the replay rebuilds the partial text from the journal alone");
    let replayed = state(&r);
    assert_eq!(replayed.0, live.0, "the engine's entities");
    assert_eq!(replayed.1, live.1, "the stored rows");
    expr::set_clock(None);
    let _ = std::fs::remove_dir_all(&dir);
}

const STREAMER: &str = "PACK st 1
Request.reply getter: {'status': 200, 'stream': True}
Request.reply on-setter go:
  ALWAYS:
    this.stream = {'id': 's', 'headers': {'x-t': 'yes'}}
Request.stream_rc on-setter hello:
  WHEN this.stream_rc == 0:
    this.send = {'id': 'm1', 'event': 'hello', 'data': {'n': 1}}
";

/// the stream verbs: `stream` answers with the head and keeps the connection, `send` appends `event:`/`data:` frames in write order, `end` closes
#[test]
fn a_request_streams_head_then_frames_in_write_order_and_end_closes() {
    use_clock(0);
    let h = Host::open({ let mut o = Options::new(None); o.plain = true; o.clock = ticking(); o }).unwrap();
    h.load_pack("st", STREAMER).unwrap();
    h.rebuild();
    h.born("Http:door", "Http", Map::new(), host::BOOT_WRITER);
    h.write("Http:door", "listen", json!({"id": "l1", "port": 0}), host::BOOT_WRITER);
    pump_until(&h, |h| !h.get("Http:door", "listen_result").is_null());
    let port = h.get("Http:door", "listen_result")["port"].as_u64().unwrap();
    let rx = client(port, "/s");
    pump_until(&h, |h| h.snapshot().iter().any(|(k, v)| k.starts_with("Request:") && !v["props"]["send_result"].is_null()));
    assert!(rx.try_recv().is_err(), "the response is still open: the client has not hit end of stream");
    let req = h.snapshot().keys().find(|k| k.starts_with("Request:")).unwrap().clone();
    assert_eq!(h.get(&req, "stream_result")["rc"], json!(0));
    h.write(&req, "send", json!({"id": "m2", "data": "two"}), "person");
    h.write(&req, "send", json!({"id": "m3", "event": "bye", "data": [1, 2]}), "person");
    h.write(&req, "end", json!({"id": "e"}), "person");
    pump_until(&h, |h| !h.get(&req, "end_result").is_null());
    let got = rx.recv_timeout(Duration::from_secs(10)).expect("the client reads to the end once closed");
    let (head, body) = got.split_once("\r\n\r\n").unwrap();
    assert!(head.starts_with("HTTP/1.1 200 OK"), "{head}");
    let lh = head.to_ascii_lowercase();
    assert!(lh.contains("content-type: text/event-stream") && lh.contains("x-t: yes") && lh.contains("cache-control: no-store") && !lh.contains("content-length"), "{head}");
    assert_eq!(body, "event: hello\ndata: {\"n\":1}\n\ndata: \"two\"\n\nevent: bye\ndata: [1,2]\n\n");
    // a send after the end says there is no open stream (a receipt, not a hang)
    h.write(&req, "send", json!({"id": "late", "data": 1}), "person");
    pump_until(&h, |h| h.get(&req, "send_result")["id"] == json!("late"));
    assert_eq!(h.get(&req, "send_result")["rc"], json!(2));
    expr::set_clock(None);
}

/// one `send` may carry SEVERAL frames (`frames: [{event?, data}, ...]`): they are queued in that order, as if each had been a send of its own
/// (a producer that has three things to say in one round writes the stream once: two setters of one round writing `send` would drop the second)
#[test]
fn one_send_may_carry_several_frames_that_arrive_in_order() {
    use_clock(0);
    let h = Host::open({ let mut o = Options::new(None); o.plain = true; o.clock = ticking(); o }).unwrap();
    h.load_pack("st", STREAMER).unwrap();
    h.rebuild();
    h.born("Http:door", "Http", Map::new(), host::BOOT_WRITER);
    h.write("Http:door", "listen", json!({"id": "l1", "port": 0}), host::BOOT_WRITER);
    pump_until(&h, |h| !h.get("Http:door", "listen_result").is_null());
    let port = h.get("Http:door", "listen_result")["port"].as_u64().unwrap();
    let rx = client(port, "/s");
    pump_until(&h, |h| h.snapshot().iter().any(|(k, v)| k.starts_with("Request:") && !v["props"]["send_result"].is_null()));
    let req = h.snapshot().keys().find(|k| k.starts_with("Request:")).unwrap().clone();
    h.write(&req, "send", json!({"id": "many", "frames": [{"event": "a", "data": 1}, {"data": "two"}, {"event": "c", "data": {"k": [3]}}]}), "person");
    pump_until(&h, |h| h.get(&req, "send_result")["id"] == json!("many"));
    assert_eq!(h.get(&req, "send_result")["rc"], json!(0));
    h.write(&req, "send", json!({"id": "bad", "frames": [{"event": "x\ny", "data": 1}]}), "person");
    pump_until(&h, |h| h.get(&req, "send_result")["id"] == json!("bad"));
    assert_eq!(h.get(&req, "send_result")["rc"], json!(2), "a frame with a bad event name refuses the whole send");
    h.write(&req, "end", json!({"id": "e"}), "person");
    pump_until(&h, |h| !h.get(&req, "end_result").is_null());
    let got = rx.recv_timeout(Duration::from_secs(10)).expect("the client reads to the end once closed");
    let (_, body) = got.split_once("\r\n\r\n").unwrap();
    assert_eq!(body, "event: hello\ndata: {\"n\":1}\n\nevent: a\ndata: 1\n\ndata: \"two\"\n\nevent: c\ndata: {\"k\":[3]}\n\n");
    expr::set_clock(None);
}
