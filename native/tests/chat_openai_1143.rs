//! The OpenAI-compatible chat rung (#1143, step 1b of the release swap): a chat turn answered by a model that is NOT a claude rung (a `cloud_models`
//! entry with another `backend`), through packs/chat_oai.c1f: one streamed call to `<base_url>/v1/chat/completions`, the door's field tools offered as
//! function tools, each tool call run through the SAME `/mcp tools/call`, the results sent back, until a text answer or `chat_max_turns`.
//! There is NEVER a real endpoint, a real key or a real `claude` here: the endpoint is a tiny HTTP server in this file (`Fake`), on 127.0.0.1.
#![cfg(feature = "bin")]
use serde_json::{json, Value as J};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const BIN: &str = env!("CARGO_BIN_EXE_cliffvm");
const KEY: &str = "sk-oai-PLANTED-0123456789-abcdefghij";
const PACKS: &[&str] = &["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f", "router.c1f", "chat.c1f", "chat_turn.c1f", "chat_oai.c1f"];

struct Run { child: Child, _lines: Receiver<String>, dir: std::path::PathBuf }
impl Drop for Run { fn drop(&mut self) { let _ = self.child.kill(); let _ = self.child.wait(); let _ = std::fs::remove_dir_all(&self.dir); } }

fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("cliffvm-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}

// ---- the fake endpoint -------------------------------------------------------------------------------------------------------------------------
/// what the fake answers a request with
enum Reply {
    /// a 200 event stream: one `data:` line per json, then `[DONE]`; `pause_ms` between the events (each event is cut in two writes)
    Sse(Vec<J>, u64),
    /// a plain answer with this status and body
    Status(u16, String),
}
struct Seen { path: String, head: String, body: J }
struct Fake { port: u16, seen: Arc<Mutex<Vec<Seen>>> }
impl Fake {
    fn start(script: impl Fn(usize, &J) -> Reply + Send + 'static) -> Fake {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let seen: Arc<Mutex<Vec<Seen>>> = Arc::new(Mutex::new(vec![]));
        let seen2 = seen.clone();
        std::thread::spawn(move || {
            for s in l.incoming().flatten() {
                let mut s = s;
                let mut r = BufReader::new(s.try_clone().unwrap());
                let (mut head, mut len) = (String::new(), 0usize);
                let mut first = String::new();
                r.read_line(&mut first).unwrap_or(0);
                head.push_str(&first);
                loop {
                    let mut l = String::new();
                    if r.read_line(&mut l).unwrap_or(0) == 0 || l == "\r\n" { break; }
                    if let Some(v) = l.to_ascii_lowercase().strip_prefix("content-length:") { len = v.trim().parse().unwrap_or(0); }
                    head.push_str(&l);
                }
                let mut body = vec![0u8; len];
                let _ = r.read_exact(&mut body);
                let body: J = serde_json::from_slice(&body).unwrap_or(J::Null);
                let path = first.split_whitespace().nth(1).unwrap_or("").to_string();
                let n = { let mut g = seen2.lock().unwrap(); g.push(Seen { path, head: head.to_lowercase(), body: body.clone() }); g.len() };
                match script(n, &body) {
                    Reply::Status(code, text) => { let _ = write!(s, "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}", text.len()); }
                    Reply::Sse(events, pause) => {
                        let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n");
                        for e in events {
                            let line = format!("data: {e}\n\n");
                            let cut = line.len() / 2;
                            let _ = s.write_all(&line.as_bytes()[..cut]); let _ = s.flush();
                            if pause > 0 { std::thread::sleep(Duration::from_millis(pause)); }
                            let _ = s.write_all(&line.as_bytes()[cut..]); let _ = s.flush();
                            if pause > 0 { std::thread::sleep(Duration::from_millis(pause)); }
                        }
                        let _ = s.write_all(b"data: [DONE]\n\n");
                    }
                }
                let _ = s.flush();
            }
        });
        Fake { port, seen }
    }
    fn calls(&self) -> usize { self.seen.lock().unwrap().len() }
    fn body(&self, i: usize) -> J { self.seen.lock().unwrap()[i].body.clone() }
}
fn delta(d: J, finish: J) -> J { json!({"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}) }
fn words(parts: &[&str]) -> Vec<J> {
    let mut v: Vec<J> = parts.iter().map(|p| delta(json!({"content": p}), J::Null)).collect();
    v.push(delta(json!({}), json!("stop")));
    v
}
/// a streamed tool call: the arguments arrive in three pieces
fn tool_round(id: &str, name: &str, args: &str) -> Vec<J> {
    let a = args.len() / 3; let b = 2 * a;
    vec![
        delta(json!({"role": "assistant", "content": null, "tool_calls": [{"index": 0, "id": id, "type": "function", "function": {"name": name, "arguments": ""}}]}), J::Null),
        delta(json!({"tool_calls": [{"index": 0, "function": {"arguments": &args[..a]}}]}), J::Null),
        delta(json!({"tool_calls": [{"index": 0, "function": {"arguments": &args[a..b]}}]}), J::Null),
        delta(json!({"tool_calls": [{"index": 0, "function": {"arguments": &args[b..]}}]}), J::Null),
        delta(json!({}), json!("tool_calls")),
    ]
}

// ---- the seat ----------------------------------------------------------------------------------------------------------------------------------
fn seat(tag: &str, base_url: &str, key_env: Option<(&str, &str)>, extra: &[&str]) -> (Run, u16, std::path::PathBuf) {
    let dir = tmp(tag);
    let pd = dir.join("packs");
    std::fs::create_dir_all(&pd).unwrap();
    let src = format!("{}/../membrane/packs", env!("CARGO_MANIFEST_DIR"));
    for n in PACKS { std::fs::write(pd.join(n), std::fs::read_to_string(format!("{src}/{n}")).unwrap_or_else(|e| panic!("{n}: {e}"))).unwrap(); }
    std::fs::copy(format!("{}/../membrane/apps/barest.c1f/default_banner.c1f", env!("CARGO_MANIFEST_DIR")), pd.join("default_banner.c1f")).unwrap();
    let entry = match key_env {
        Some((var, _)) => json!([{"name": "gpt-fake", "backend": "openai", "base_url": base_url, "api_key_env": var}]),
        None => json!([{"name": "gpt-fake", "backend": "openai", "base_url": base_url}]),
    };
    let mut cmd = Command::new(BIN);
    cmd.current_dir(&dir)
        .args(["run", pd.join("boot.c1f").to_str().unwrap(), pd.to_str().unwrap(), "--store", dir.join("field.sqlite3").to_str().unwrap(), "--port", "0", "--ui-dir", dir.to_str().unwrap()])
        .args(["--fact", &format!("cloud_models={entry}"), "--fact", "router_on=false", "--fact", "seat_backend=http://127.0.0.1:1",
               "--fact", r#"minds={"loops":"gpt-fake","cortex":"gpt-fake","workers":"gpt-fake","seat_default":"gpt-fake"}"#])
        .args(extra);
    for v in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FAKE_OAI_KEY"] { cmd.env_remove(v); }
    if let Some((var, val)) = key_env { cmd.env(var, val); }
    let err = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("seat.err")).unwrap();
    let mut child = cmd.stdout(Stdio::piped()).stderr(Stdio::from(err)).spawn().unwrap();
    let out = child.stdout.take().unwrap();
    let (tx, rx) = channel();
    let log = dir.join("seat.out");
    std::thread::spawn(move || { let mut f = std::fs::OpenOptions::new().create(true).append(true).open(log).unwrap(); for l in BufReader::new(out).lines().map_while(Result::ok) { let _ = writeln!(f, "{l}"); if tx.send(l).is_err() { break; } } });
    let run = Run { child, _lines: rx, dir: dir.clone() };
    let line = run._lines.recv_timeout(Duration::from_secs(30)).expect("the door came up");
    let port: u16 = line.rsplit(':').next().unwrap().trim().parse().unwrap();
    (run, port, dir)
}
/// a seat whose model endpoint is `fake`, with the planted key in FAKE_OAI_KEY
fn seat_on(tag: &str, fake: &Fake, extra: &[&str]) -> (Run, u16, std::path::PathBuf) {
    seat(tag, &format!("http://127.0.0.1:{}", fake.port), Some(("FAKE_OAI_KEY", KEY)), extra)
}

fn http(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(60))).unwrap();
    let raw = format!("{method} {path} HTTP/1.1\r\nHost: t\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    s.write_all(raw.as_bytes()).unwrap();
    let mut out = vec![];
    let _ = s.read_to_end(&mut out);
    let i = out.windows(4).position(|w| w == b"\r\n\r\n").expect("an HTTP answer");
    let status: u16 = String::from_utf8_lossy(&out[..i]).split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, String::from_utf8_lossy(&out[i + 4..]).to_string())
}
fn call(port: u16, tool: &str, args: J) -> (String, bool) {
    let (st, body) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}).to_string());
    assert_eq!(st, 200, "{body}");
    let r: J = serde_json::from_str(&body).unwrap();
    (r["result"]["content"][0]["text"].as_str().unwrap_or_else(|| panic!("{r}")).to_string(), r["result"]["isError"].as_bool().unwrap())
}
fn wait<T>(what: &str, mut f: impl FnMut() -> Option<T>) -> T {
    let t = Instant::now();
    loop {
        if let Some(v) = f() { return v; }
        assert!(t.elapsed() < Duration::from_secs(60), "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(100));
    }
}
fn terminate(run: &mut Run) -> bool {
    unsafe { libc::kill(run.child.id() as i32, libc::SIGTERM); }
    let t = Instant::now();
    while t.elapsed() < Duration::from_secs(15) {
        if run.child.try_wait().unwrap().is_some() { return true; }
        std::thread::sleep(Duration::from_millis(50));
    }
    false
}
fn send(port: u16, text: &str) -> String {
    let (st, body) = http(port, "POST", "/membrane/chat/message", &json!({"text": text}).to_string());
    assert_eq!(st, 202, "{body}");
    serde_json::from_str::<J>(&body).unwrap()["id"].as_str().unwrap().to_string()
}
fn view(port: u16, id: &str) -> J {
    let (st, body) = http(port, "GET", &format!("/membrane/chat/message/{id}"), "");
    assert_eq!(st, 200, "{body}");
    serde_json::from_str(&body).unwrap()
}
fn ended(port: u16, id: &str) -> J { wait("the turn to end", || { let v = view(port, id); if v["state"] == "done" || v["state"] == "error" { Some(v) } else { None } }) }

// ---- the tests ---------------------------------------------------------------------------------------------------------------------------------

#[test]
fn a_plain_answer_turn_is_one_streamed_call_with_the_doctrine_the_message_and_the_field_tools() {
    let fake = Fake::start(|_, _| {
        let mut ev = words(&["Hel", "lo ", "there"]);
        ev.push(json!({"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}}));
        Reply::Sse(ev, 0)
    });
    let (mut run, port, _dir) = seat_on("oai-plain", &fake, &[]);
    let id = send(port, "say hello");
    let v = ended(port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "Hello there");
    assert_eq!(v["tools"], json!([]));
    assert_eq!(fake.calls(), 1, "one call: no tool, no second round");
    let (seen_path, seen_head) = { let g = fake.seen.lock().unwrap(); (g[0].path.clone(), g[0].head.clone()) };
    assert_eq!(seen_path, "/v1/chat/completions");
    assert!(seen_head.contains(&format!("authorization: bearer {}", KEY.to_lowercase())), "the key rides as a bearer: {seen_head}");
    let b = fake.body(0);
    assert_eq!(b["model"], "gpt-fake");
    assert_eq!(b["stream"], json!(true));
    let m = b["messages"].as_array().unwrap();
    assert_eq!(m.len(), 2, "the system prompt and the message, no history: {b}");
    assert_eq!(m[0]["role"], "system");
    let sys = m[0]["content"].as_str().unwrap();
    assert!(sys.contains("Modify your banner to help you maintain continuity.") && sys.contains("CONVERSATION: main"), "the doctrine and the banner, as the claude turn gets them: {sys}");
    assert_eq!(m[1], json!({"role": "user", "content": "say hello"}));
    let names: Vec<&str> = b["tools"].as_array().unwrap().iter().map(|t| t["function"]["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["entity_query", "entity_read", "entity_write", "run_cliff"]);
    // the schemas are the door's own
    let (_, tl) = http(port, "POST", "/mcp", &json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).to_string());
    let tl: J = serde_json::from_str(&tl).unwrap();
    for (i, t) in tl["result"]["tools"].as_array().unwrap().iter().enumerate() {
        assert_eq!(b["tools"][i], json!({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}));
    }
    assert_eq!(v["turn"]["usage"], json!({"input_tokens": 11, "output_tokens": 3}), "the endpoint's own numbers: {v}");
    assert!(terminate(&mut run));
}

#[test]
fn a_tool_call_runs_through_the_doors_own_tool_logic_and_its_result_goes_back_to_the_model() {
    let fake = Fake::start(|n, _| if n == 1 {
        Reply::Sse(tool_round("call_a1", "entity_write", r#"{"spec":"Thing:probe","prop":"x","value":5}"#), 0)
    } else { Reply::Sse(words(&["It is ", "written."]), 0) });
    let (mut run, port, _dir) = seat_on("oai-tool", &fake, &[]);
    let id = send(port, "write it");
    let v = ended(port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "It is written.");
    assert_eq!(v["tools"], json!(["entity_write"]));
    assert_eq!(fake.calls(), 2);
    let (t, e) = call(port, "entity_read", json!({"spec": "Thing:probe", "prop": "x"}));
    assert!(!e && t == "5", "the write reached the field: {t}");
    // round two: the conversation so far, the assistant's call, the door's own text as the tool result
    let m = fake.body(1)["messages"].clone();
    let m = m.as_array().unwrap();
    assert_eq!(m.len(), 4, "{m:?}");
    assert_eq!(m[1]["content"], "write it");
    assert_eq!(m[2]["role"], "assistant");
    assert_eq!(m[2]["tool_calls"][0], json!({"id": "call_a1", "type": "function", "function": {"name": "entity_write", "arguments": r#"{"spec":"Thing:probe","prop":"x","value":5}"#}}));
    assert_eq!(m[3]["role"], "tool");
    assert_eq!(m[3]["tool_call_id"], "call_a1");
    assert!(m[3]["content"].as_str().unwrap().starts_with("wrote Thing:probe.x"), "the same reply text as /mcp tools/call gives: {m:?}");
    let told = m[3]["content"].as_str().unwrap().to_string();
    // the tool is an entity under the turn, like the claude turn's
    let (n, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn:tool:call_a1"), "prop": "name"}));
    assert_eq!(n, "entity_write");
    let (r, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn:tool:call_a1"), "prop": "result"}));
    assert_eq!(r, told, "the tool entity holds the very text the model got");
    assert!(terminate(&mut run));
}

#[test]
fn the_answer_is_streamed_into_the_event_stream_as_it_arrives() {
    let fake = Fake::start(|_, _| Reply::Sse(words(&["one ", "two ", "three"]), 250));
    let (mut run, port, _dir) = seat_on("oai-stream", &fake, &[]);
    let id = send(port, "count");
    let (st, body) = http(port, "GET", &format!("/membrane/chat/message/{id}/events"), "");
    assert_eq!(st, 200);
    let evs: Vec<J> = body.lines().filter_map(|l| l.strip_prefix("data:")).filter_map(|d| serde_json::from_str(d.trim()).ok()).collect();
    let partial: Vec<&str> = evs.iter().filter(|e| e["state"] == "running").filter_map(|e| e["response"].as_str()).collect();
    assert!(partial.iter().any(|p| !p.is_empty() && p.len() < "one two three".len() && "one two three".starts_with(p)), "a running event shows a part of the answer: {partial:?}");
    let last = evs.last().unwrap();
    assert_eq!((last["state"].as_str(), last["response"].as_str()), (Some("done"), Some("one two three")), "{last}");
    assert!(terminate(&mut run));
}

#[test]
fn a_tool_error_is_the_tool_result_the_model_gets_and_a_call_that_is_not_json_is_told_so() {
    let fake = Fake::start(|n, _| if n == 1 {
        let mut ev = tool_round("c1", "entity_read", "{}");
        ev.insert(4, delta(json!({"tool_calls": [{"index": 1, "id": "c2", "type": "function", "function": {"name": "entity_read", "arguments": "{oops"}}]}), J::Null));
        Reply::Sse(ev, 0)
    } else { Reply::Sse(words(&["seen"]), 0) });
    let (mut run, port, _dir) = seat_on("oai-toolerr", &fake, &[]);
    let id = send(port, "break it");
    let v = ended(port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert_eq!(v["response"], "seen");
    let (door_text, door_err) = call(port, "entity_read", json!({}));
    assert!(door_err);
    let m = fake.body(1)["messages"].clone();
    let m = m.as_array().unwrap();
    assert_eq!(m.len(), 5, "{m:?}");
    assert_eq!((m[3]["role"].as_str(), m[3]["tool_call_id"].as_str(), m[3]["content"].as_str()), (Some("tool"), Some("c1"), Some(door_text.as_str())), "the door's error text, to the model: {m:?}");
    assert_eq!((m[4]["role"].as_str(), m[4]["tool_call_id"].as_str()), (Some("tool"), Some("c2")));
    assert!(m[4]["content"].as_str().unwrap().starts_with("Error: ") && m[4]["content"].as_str().unwrap().contains("not a JSON object"), "{m:?}");
    assert!(terminate(&mut run));
}

#[test]
fn an_endpoint_that_is_down_or_refuses_is_a_failed_turn_whose_cause_is_stored() {
    // nobody listens: the connection is refused
    let (mut run, port, _dir) = seat("oai-down", "http://127.0.0.1:1", Some(("FAKE_OAI_KEY", KEY)), &[]);
    let id = send(port, "anyone there");
    let v = ended(port, &id);
    assert_eq!(v["state"], "error", "{v}");
    let cause = v["response"].as_str().unwrap();
    assert!(cause.contains("gpt-fake") && cause.contains("connect") && cause.contains("127.0.0.1:1"), "the cause names the model and what failed: {cause}");
    let (e, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn"), "prop": "error"}));
    assert_eq!(e, cause, "the turn stores the cause");
    let (r, _) = call(port, "entity_read", json!({"spec": format!("membrane::Chat:message:{id}:turn:response"), "prop": "error"}));
    assert_eq!(r, "True");
    assert!(terminate(&mut run));
    // the endpoint answers 500
    let fake = Fake::start(|_, _| Reply::Status(500, r#"{"error":{"message":"the model is overloaded"}}"#.into()));
    let (mut run, port, _dir) = seat_on("oai-500", &fake, &[]);
    let id = send(port, "hello");
    let v = ended(port, &id);
    assert_eq!(v["state"], "error", "{v}");
    let cause = v["response"].as_str().unwrap();
    assert!(cause.contains("500") && cause.contains("the model is overloaded"), "{cause}");
    assert!(terminate(&mut run));
}

#[test]
fn a_missing_key_variable_or_base_url_is_a_failed_turn_that_says_what_to_set() {
    let fake = Fake::start(|_, _| Reply::Sse(words(&["x"]), 0));
    // the variable the entry names is not in the seat's environment
    let (mut run, port, _dir) = seat("oai-nokey", &format!("http://127.0.0.1:{}", fake.port), Some(("FAKE_OAI_KEY", "")), &[]);
    let id = send(port, "hello");
    let v = ended(port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap().contains("FAKE_OAI_KEY"), "{v}");
    assert_eq!(fake.calls(), 0, "nothing was sent without the key");
    assert!(terminate(&mut run));
    // the entry names no base_url
    let (mut run, port, _dir) = seat("oai-nourl", "", Some(("FAKE_OAI_KEY", KEY)), &[]);
    let id = send(port, "hello");
    let v = ended(port, &id);
    assert_eq!(v["state"], "error", "{v}");
    assert!(v["response"].as_str().unwrap().contains("base_url"), "{v}");
    assert!(terminate(&mut run));
}

/// every file the seat left in its folder: nothing in it may hold `needle`
fn assert_nowhere_on_disk(dir: &std::path::Path, needle: &str) {
    fn walk(d: &std::path::Path, out: &mut Vec<std::path::PathBuf>) { for e in std::fs::read_dir(d).unwrap().flatten() { let p = e.path(); if p.is_dir() { walk(&p, out) } else { out.push(p) } } }
    let mut files = vec![];
    walk(dir, &mut files);
    let mut seen_store = false;
    for f in files {
        if f.file_name().unwrap().to_string_lossy().starts_with("field.sqlite3") { seen_store = true; }
        let bytes = std::fs::read(&f).unwrap_or_default();
        assert!(!bytes.windows(needle.len()).any(|w| w == needle.as_bytes()), "the key is in {}", f.display());
    }
    assert!(seen_store, "the store was looked at");
}

#[test]
fn the_key_comes_from_the_environment_and_is_in_no_row_journal_log_route_or_answer_even_when_the_endpoint_echoes_it() {
    // the endpoint echoes the key in a 401 page on the second message (a debug line, an auth error)
    let fake = Fake::start(|n, _| if n == 1 { Reply::Sse(words(&["fine"]), 0) } else { Reply::Status(401, format!(r#"{{"error":"invalid api key: {KEY}"}}"#)) });
    let (mut run, port, dir) = seat_on("oai-key", &fake, &[]);
    let id = send(port, "first");
    let v = ended(port, &id);
    assert_eq!(v["state"], "done", "{v}");
    let id2 = send(port, "second");
    let v2 = ended(port, &id2);
    assert_eq!(v2["state"], "error", "{v2}");
    assert!(v2["response"].as_str().unwrap().contains("401") && v2["response"].as_str().unwrap().contains("***"), "the endpoint's words stay, the key is masked: {v2}");
    let seen_head = fake.seen.lock().unwrap()[0].head.clone();
    assert!(seen_head.contains(&KEY.to_lowercase()), "the endpoint did get the key");
    for p in [format!("/membrane/chat/message/{id}"), format!("/membrane/chat/message/{id2}"), "/membrane/chat".to_string(), "/membrane/options".to_string(), "/v1/models".to_string(), "/session".to_string(), "/membrane/chat/messages".to_string()] {
        let (_, b) = http(port, "GET", &p, "");
        assert!(!b.contains(KEY), "the key is in the reply of {p}: {b}");
    }
    for q in ["kind:Fetch", "kind:Boot", "kind:ChatMessage", "kind:Chat", "kind:ChatRound", "kind:ChatTurn", "kind:Model"] {
        let (t, _) = call(port, "entity_query", json!({"q": q, "full": true}));
        assert!(!t.contains(KEY), "the key is in {q}: {t}");
    }
    for spec in [format!("membrane::Chat:message:{id2}:turn"), "Boot:seat".to_string(), format!("Fetch:oai:{id}:1")] {
        let (t, _) = call(port, "entity_read", json!({"spec": spec}));
        assert!(!t.contains(KEY), "the key is in {spec}: {t}");
    }
    assert!(terminate(&mut run));
    assert_nowhere_on_disk(&dir, KEY);
}

#[test]
fn the_loop_stops_at_chat_max_turns_with_a_visible_cause() {
    let fake = Fake::start(|_, _| Reply::Sse(tool_round("again", "entity_query", r#"{"q":"kind:Boot","count":true}"#), 0));
    let (mut run, port, _dir) = seat_on("oai-max", &fake, &["--fact", "chat_max_turns=2"]);
    let id = send(port, "go on forever");
    let v = ended(port, &id);
    assert_eq!(v["state"], "done", "{v}");
    assert!(v["response"].as_str().unwrap().contains("(stopped at the ceiling of 2 turns)"), "{v}");
    assert_eq!(fake.calls(), 2, "the model was called twice and no more");
    assert!(terminate(&mut run));
}
