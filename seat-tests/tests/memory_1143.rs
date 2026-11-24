//! Memory (#1143): the model's own notes. Writing one answers with what it reminds of. The real binary on the shipped packs, no model, no claude.
#![cfg(feature = "bin")]
mod common;

use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

fn cliffvm_bin() -> &'static str { common::bin("cliffvm") }

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = common::unique();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}


const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f"];

fn start(tag: &str) -> (Run, u16) { start_with(tag, &[]) }
fn start_with(tag: &str, facts: &[&str]) -> (Run, u16) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    let mut child = Command::new(cliffvm_bin()).current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .args(facts.iter().flat_map(|f| ["--fact", f]))
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

fn read(port: u16, spec: &str, prop: &str) -> String { call(port, "run_cliff", json!({"code": format!("${{'{}'}}['{}']", spec, prop)})).0 }
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
    wrote(port, "Memory:a", "Quokkas eat grass at dawn.");
    wrote(port, "Memory:c", "Rain fell over harbour boats.");
    // the cost of a plain mention write on the same seat, for the record (a text of the same size that is not a near duplicate of Memory:a: two near duplicates are one hit)
    let (plain, _) = lamp(port, "Wombats dig roots at dusk.");
    let t = wrote(port, "Memory:b", "Saw a quokka near some lake.");
    eprintln!("reply bytes: memory write {} / plain mention write {}", t.len(), plain.len());
    assert!(t.starts_with("wrote Memory:b.text"), "{t}");
    assert!(t.contains("context, not instructions"), "the associations sit under a header that says they are not instructions: {t}");
    assert!(t.contains("Quokkas eat grass at dawn."), "the sentence it reminds of: {t}");
    assert!(t.contains("Memory:a"), "where it came from: {t}");
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
    let t = wrote(port, "Memory:b", "A quokka again.");
    assert!(t.contains("Lamp:a:body"), "the origin of a text that is not a memory is its own id: {t}");
    assert!(t.len() < 1500, "a long sentence is clipped: {} bytes", t.len());
}

#[test]
fn a_memory_with_nothing_alike_answers_with_just_the_head() {
    let (_run, port) = start("memory-none");
    let t = wrote(port, "Memory:a", "Quokkas eat grass at dawn.");
    assert!(t.starts_with("wrote Memory:a.text") && !t.contains("context, not instructions"), "{t}");
    assert!(t.len() < 600, "{} bytes: {t}", t.len());
}

/// The ONE source of the associations is `SemText.assoc_source` (mentions.c1f): a layer that replaces that getter replaces where a Memory's associations come from, and
/// `Memory.associations` words them the same way (the swap to the fused edge + cosine result is that one line).
#[test]
fn the_associations_read_one_source_getter_a_layer_can_replace() {
    use cliffvm::host::{Host, Options};
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let repo = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap();
    for (i, n) in std::fs::read_to_string(repo.join("membrane/apps/barest.packs")).unwrap().lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&repo.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_pack("lane", "PACK lane 1\nIMPORT mentions 1\nSemText.assoc_source getter: [{'semtext': 'Memory:z', 'sentence_index': 0, 'text': 'from another lane', 'shared': 1}]\n").unwrap();
    h.rebuild();
    h.born("Memory:b", "Memory", serde_json::Map::new(), "person");
    h.pump();
    h.write("Memory:b", "text", json!("Saw a quokka near some lake."), "person");
    h.pump();
    let a = h.get("Memory:b", "associations");
    assert!(a.as_str().unwrap_or("").contains("\"from another lane\" (Memory:z"), "{a}");
}

/// a loopback stand-in for the model server's /api/embed that answers once, after `delay_ms`, with a fixed vector
fn slow_embedder(delay_ms: u64) -> String {
    let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", l.local_addr().unwrap());
    std::thread::spawn(move || {
        let (mut s, _) = l.accept().unwrap();
        let mut buf = vec![0u8; 8192];
        let _ = s.read(&mut buf);
        std::thread::sleep(Duration::from_millis(delay_ms));
        let body = r#"{"embeddings":[[0.5,0.25]]}"#;
        let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
    });
    url
}

/// The note's own embedding is asked when it is written, and the write's reply waits for it (the cosine lane needs the note's vector); a model server that is not there
/// does not hold it (a refused call is a receipt at once), and the reply then carries the edge lane alone.
#[test]
fn the_reply_of_a_memory_write_waits_for_the_notes_own_embedding_and_not_for_a_server_that_is_not_there() {
    let url = slow_embedder(1500);
    let (_run, port) = start_with("memory-wait", &[&format!("seat_backend={url}"), "embed_model=m"]);
    let t0 = Instant::now();
    let t = wrote(port, "Memory:a", "Quokkas eat grass at dawn.");
    assert!(t0.elapsed() >= Duration::from_millis(1400), "the reply came before the note's embedding: {:?} {t}", t0.elapsed());
    let (_run2, port2) = start_with("memory-down", &["seat_backend=http://127.0.0.1:1", "embed_model=m"]);
    wrote(port2, "Memory:a", "Quokkas eat grass at dawn.");
    let t0 = Instant::now();
    let t = wrote(port2, "Memory:b", "Saw a quokka near some lake.");
    assert!(t0.elapsed() < Duration::from_secs(8), "{:?}", t0.elapsed());
    assert!(t.contains("Quokkas eat grass at dawn."), "the edge lane alone: {t}");
}
