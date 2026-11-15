//! MemoryNote (#1143): the model's own notes. Writing one answers with what it reminds of. The real binary on the shipped packs, no model, no claude.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}


const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f"];

fn start(tag: &str) -> (Run, u16) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    let mut child = Command::new(BIN).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    std::thread::spawn(move || for l in BufReader::new(out).lines().map_while(Result::ok) { if tx.send(l).is_err() { break; } });
    let run = Run { child, _lines: rx, dir };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up (a line on stdout)");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap_or_else(|_| panic!("a port in {line:?}"));
    (run, port)
}

fn http(port: u16, body: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    let raw = format!("POST /mcp HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    String::from_utf8_lossy(&out[i + 4..]).to_string()
}

fn call(port: u16, tool: &str, args: J) -> (String, bool) {
    let body = http(port, &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    let r: J = serde_json::from_str(&body).unwrap_or_else(|e| panic!("{e}: {body}"));
    (r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string(), r["result"]["isError"].as_bool().unwrap())
}

fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(40), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "entity_read", json!({"spec": spec, "prop": prop})).0 }
fn write(port: u16, spec: &str, prop: &str, v: J) { let (t, e) = call(port, "entity_write", json!({"spec": spec, "prop": prop, "value": v})); assert!(!e, "{t}"); }
fn read_until(port: u16, spec: &str, prop: &str, want: &str) -> String {
    wait(&format!("{spec}.{prop} = {want}"), || { let t = read(port, spec, prop); if t == want { Some(t) } else { None } })
}


fn wrote(port: u16, id: &str, text: &str) -> String {
    let (t, e) = call(port, "entity_write", json!({"spec": id, "prop": "text", "value": text}));
    assert!(!e, "{t}");
    t
}

/// a text of a pack the model wrote (not a memory): `Lamp:a:body` is a SemText that is mentioned; the answer of the write that made it is returned
fn lamp(port: u16, text: &str) -> (String, bool) {
    let pack = "PACK mine 1\nIMPORT semtext 1\nIMPORT mentions 1\nLamp.say on-setter make:\n  ALWAYS:\n    Lamp:a:body = SemText(text: this.say)\n    Lamp:a:body.mention = True\n";
    write(port, "Pack:mine", "text", json!(pack));
    read_until(port, "Pack:mine", "state", "loaded");
    call(port, "entity_write", json!({"spec": "Lamp:a", "prop": "say", "value": text}))
}

#[test]
fn writing_a_memory_answers_with_what_it_reminds_of_in_a_small_reply() {
    let (_run, port) = start("memory-reply");
    wrote(port, "MemoryNote:a", "Quokkas eat grass at dawn.");
    wrote(port, "MemoryNote:c", "Rain fell over harbour boats.");
    // the cost of a plain mention write on the same seat, for the record
    let (plain, _) = lamp(port, "Quokkas eat grass at dawn.");
    let t = wrote(port, "MemoryNote:b", "Saw a quokka near some lake.");
    eprintln!("reply bytes: memory write {} / plain mention write {}", t.len(), plain.len());
    assert!(t.starts_with("wrote MemoryNote:b.text"), "{t}");
    assert!(t.contains("context, not instructions"), "the associations sit under a header that says they are not instructions: {t}");
    assert!(t.contains("Quokkas eat grass at dawn."), "the sentence it reminds of: {t}");
    assert!(t.contains("MemoryNote:a"), "where it came from: {t}");
    assert!(t.contains("quokka"), "the shared entity: {t}");
    assert!(!t.contains("Rain fell"), "an unrelated memory is not listed: {t}");
    assert!(t.len() < 2048, "the reply stays small: {} bytes: {t}", t.len());
    assert!(!t.contains("\"edge_rows\"") && !t.contains("Span:of"), "no transient span noise: {t}");
}

#[test]
fn a_long_sentence_is_clipped_and_an_origin_that_is_not_a_memory_is_named() {
    let (_run, port) = start("memory-clip");
    let long = format!("Quokkas {} end.", "wander ".repeat(200));
    lamp(port, &long);
    let t = wrote(port, "MemoryNote:b", "A quokka again.");
    assert!(t.contains("Lamp:a:body"), "the origin of a text that is not a memory is its own id: {t}");
    assert!(t.len() < 1500, "a long sentence is clipped: {} bytes", t.len());
}

#[test]
fn a_memory_with_nothing_alike_answers_with_just_the_head() {
    let (_run, port) = start("memory-none");
    let t = wrote(port, "MemoryNote:a", "Quokkas eat grass at dawn.");
    assert!(t.starts_with("wrote MemoryNote:a.text") && !t.contains("context, not instructions"), "{t}");
    assert!(t.len() < 600, "{} bytes: {t}", t.len());
}

/// The ONE source of the associations is `SemText.assoc_source` (mentions.c1f): a layer that replaces that getter replaces where a MemoryNote's associations come from, and
/// `MemoryNote.associations` words them the same way (the swap to the fused edge + cosine result is that one line).
#[test]
fn the_associations_read_one_source_getter_a_layer_can_replace() {
    use membrane_vm::host::{Host, Options};
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap();
    for (i, n) in std::fs::read_to_string(repo.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&repo.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_pack("lane", "PACK lane 1\nIMPORT mentions 1\nSemText.assoc_source getter: [{'semtext': 'MemoryNote:z', 'sentence_index': 0, 'text': 'from another lane', 'shared': 1}]\n").unwrap();
    h.rebuild();
    h.born("MemoryNote:b", "MemoryNote", serde_json::Map::new(), "person");
    h.pump();
    h.write("MemoryNote:b", "text", json!("Saw a quokka near some lake."), "person");
    h.pump();
    let a = h.get("MemoryNote:b", "associations");
    assert!(a.as_str().unwrap_or("").contains("\"from another lane\" (MemoryNote:z"), "{a}");
}
