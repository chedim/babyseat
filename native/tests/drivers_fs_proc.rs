//! The stdlib drivers (#1044): fs reads as asynchronous getter built-ins; File and Command effects run by the engine
//! off its lock, their receipts landing as `<verb>_result`; entity scripts never trigger one.
use membrane_vm::fields::Fields;
use membrane_vm::store::Store;
use membrane_vm::vm::{Core, Host, Write};
use serde_json::{json, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn resolve_exact(&self, s: &str) -> Option<String> { Some(s.to_string()) }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

fn dir() -> std::path::PathBuf {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let d = std::env::temp_dir().join(format!("drivers-{}-{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
    std::fs::create_dir_all(&d).unwrap();
    d
}

const PACK: &str = "PACK std 1
File.text getter: read(this.path) if this.path else None
File.info getter: stat(this.path) if this.path else None
Dir.names getter: [prop(e, 'name') for e in (listdir(this.path) or [])] if this.path else []
Command.kind getter: 'command'
";

fn world(d: &std::path::Path) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    f.set_pack("std", PACK).unwrap();
    let mut c = Core::new();
    c.store = Some(Store::io_only(d.to_str().unwrap()));
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J, writer: &str) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: writer.into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
fn land(c: &mut Core, f: &mut Fields) {
    let w = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    while w.pending() > 0 && t.elapsed().as_secs() < 10 { w.wait(Some(std::time::Duration::from_millis(100))); let mut s = 30; f.settle(c, &H, 3.0, &mut s); }
    let mut s = 40; f.settle(c, &H, 3.0, &mut s);
}

#[test]
fn files_are_read_listed_and_statted_by_getters() {
    let d = dir();
    std::fs::write(d.join("a.txt"), "hello").unwrap();
    std::fs::create_dir(d.join("sub")).unwrap();
    let (mut c, mut f) = world(&d);
    c.load("File:a".into(), "File".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    c.load("Dir:root".into(), "Dir".into(), json!({}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "File:a", "path", json!("a.txt"), "person");               // relative: the seat's folder
    put(&mut c, &mut f, "Dir:root", "path", json!(d.to_str().unwrap()), "person");
    land(&mut c, &mut f);
    assert_eq!(c.docs["File:a"].props["text"], json!("hello"));
    assert_eq!(c.docs["File:a"].props["info"]["size"], json!(5));
    assert_eq!(c.docs["Dir:root"].props["names"], json!(["a.txt", "sub"]));
}

#[test]
fn a_file_write_is_an_effect_with_a_receipt_and_scripts_are_refused() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("File:n".into(), "File".into(), json!({"path": "notes/n.txt"}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "File:n", "write", json!({"id": "w1", "content": "line one\n"}), "person");
    assert_eq!(c.docs["File:n"].props["write"], json!(""));                           // the slot is consumed at once
    land(&mut c, &mut f);
    assert_eq!(std::fs::read_to_string(d.join("notes/n.txt")).unwrap(), "line one\n");
    assert_eq!(c.docs["File:n"].props["write_result"]["id"], json!("w1"));
    assert_eq!(c.docs["File:n"].props["write_rc"], json!(0));
    put(&mut c, &mut f, "File:n", "write", json!({"id": "w2", "content": "x"}), "script:Component:x");
    assert_eq!(c.docs["File:n"].props["write_result"]["rc"], json!(2));
    assert_eq!(std::fs::read_to_string(d.join("notes/n.txt")).unwrap(), "line one\n");
}

#[test]
fn a_command_runs_off_the_engine_and_can_be_stopped() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("Command:echo".into(), "Command".into(), json!({"cmd": ["sh", "-c", "echo hi $0 && echo oops >&2", "there"]}).as_object().unwrap().clone(), None, 0.0);
    c.load("Command:sleep".into(), "Command".into(), json!({"cmd": "sleep 30"}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "Command:echo", "run", json!({"id": "r1"}), "person");
    land(&mut c, &mut f);
    let r = &c.docs["Command:echo"].props["run_result"];
    assert_eq!(r["rc"], json!(0));
    assert_eq!(r["stdout"], json!("hi there\n"));
    assert_eq!(r["stderr"], json!("oops\n"));
    let t = std::time::Instant::now();
    put(&mut c, &mut f, "Command:sleep", "run", json!({"id": "s1"}), "person");
    assert!(t.elapsed().as_millis() < 500, "the engine waited for the command");
    std::thread::sleep(std::time::Duration::from_millis(200));
    put(&mut c, &mut f, "Command:sleep", "stop", json!({"id": "x"}), "person");
    land(&mut c, &mut f);
    assert_eq!(c.docs["Command:sleep"].props["stop_result"]["head"], json!("stopped"));
    assert_ne!(c.docs["Command:sleep"].props["run_rc"], json!(0));
    assert!(t.elapsed().as_secs() < 10);
}

/// A translated method (packs/methods.c1f) is a string `cmd` with "$1", "$2" for its {{params}}: the run's args are those words,
/// each ONE word whatever they hold (spaces, quotes, `;`), as the old shell-quoting made them.
#[test]
fn a_translated_methods_params_are_positional_words_and_stay_one_word() {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("Command:say".into(), "Command".into(), json!({"cmd": "echo \"$2\" \"$1\" \"$1\""}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "Command:say", "run", json!({"id": "r1", "args": ["a b; echo pwned", "it's"]}), "person");
    land(&mut c, &mut f);
    let r = &c.docs["Command:say"].props["run_result"];
    assert_eq!(r["rc"], json!(0));
    assert_eq!(r["stdout"], json!("it's a b; echo pwned a b; echo pwned\n"));
}

// ---- no work under a temporary folder (the operator, 2026-11-12: a battery restart wiped work done under /tmp) ----

fn refused_write(path: &str) -> J {
    let d = dir();
    let (mut c, mut f) = world(&d);
    c.load("File:w".into(), "File".into(), json!({"path": path}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:w", "write", json!({"id": "t", "content": "x"}), "model");
    land(&mut c, &mut f);
    c.docs["File:w"].props.get("write_result").cloned().unwrap_or(J::Null)
}

#[test]
fn a_file_is_not_written_under_a_temporary_folder() {
    for p in ["/tmp/work/a.txt", "/private/tmp/work/a.txt", "/var/tmp/a.txt", "/var/folders/xx/T/a.txt", "/private/var/folders/xx/T/a.txt"] {
        let r = refused_write(p);
        assert_eq!(r["rc"], json!(2), "{p}: {r}");
        assert!(r["head"].as_str().unwrap().contains("temporary folder"), "{r}");
        assert!(!std::path::Path::new(p).exists(), "{p} was written");
    }
}

#[test]
fn a_move_or_delete_into_or_out_of_a_temporary_folder_is_refused_too() {
    let d = dir();
    std::fs::write(d.join("keep.txt"), "x").unwrap();
    let (mut c, mut f) = world(&d);
    c.load("File:m".into(), "File".into(), json!({"path": d.join("keep.txt").to_str().unwrap()}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:m", "move", json!({"id": "m", "to": "/tmp/elsewhere.txt"}), "model");
    land(&mut c, &mut f);
    let r = c.docs["File:m"].props.get("move_result").cloned().unwrap();
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(d.join("keep.txt").exists());
}

#[test]
fn the_seats_own_folder_may_be_a_temporary_one_and_reading_a_temporary_file_is_fine() {
    let d = dir();                                          // a throwaway seat's data folder lives under the OS temp dir
    let (mut c, mut f) = world(&d);
    c.load("File:ok".into(), "File".into(), json!({"path": d.join("ok.txt").to_str().unwrap()}).as_object().unwrap().clone(), None, 0.0);
    put(&mut c, &mut f, "File:ok", "write", json!({"id": "w", "content": "fine"}), "model");
    land(&mut c, &mut f);
    assert_eq!(c.docs["File:ok"].props["write_result"]["rc"], json!(0));
    assert_eq!(std::fs::read_to_string(d.join("ok.txt")).unwrap(), "fine");
}

fn run_result(cwd: Option<&str>, args: J) -> J {
    let d = dir();
    let (mut c, mut f) = world(&d);
    let mut p = json!({"cmd": ["echo"]}).as_object().unwrap().clone();
    if let Some(w) = cwd { p.insert("cwd".into(), json!(w)); }
    c.load("Command:c".into(), "Command".into(), p, None, 0.0);
    put(&mut c, &mut f, "Command:c", "run", json!({"id": "r", "args": args}), "model");
    land(&mut c, &mut f);
    c.docs["Command:c"].props.get("run_result").cloned().unwrap_or(J::Null)
}

#[test]
fn a_command_does_not_run_in_or_on_a_temporary_folder() {
    let r = run_result(Some("/tmp"), json!(["hi"]));
    assert_eq!(r["rc"], json!(2), "{r}");
    assert!(r["head"].as_str().unwrap().contains("temporary folder"));
    let r = run_result(None, json!(["clone", "/private/tmp/wt"]));
    assert_eq!(r["rc"], json!(2), "{r}");
    let r = run_result(None, json!(["hello", "world"]));
    assert_eq!(r["rc"], json!(0), "{r}");
}

// ---- HANDED-OVER SECRETS (#1143, the key's secrecy): never in a chunk, a receipt or a row, split across reads or not ------------------------
const SECRET: &str = "Zq7#kP9wXv2LmN4tRb8Yc";
/// run a command that gets SECRET as $SK (env_from names the variable; the entity's `cmd` never holds the value); `partial` asks for chunks;
/// returns (the receipt, every Chunk entity's props, the whole world's rows as text)
fn run_secret(var: &str, value: &str, script: &str) -> (J, Vec<J>, String) {
    let d = dir();
    let (mut c, mut f) = world(&d);
    std::env::set_var(var, value);
    c.load("Command:sec".into(), "Command".into(), json!({"cmd": ["sh", "-c", script], "env_from": {"SK": var}}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "Command:sec", "run", json!({"id": "r1", "partial": true}), "person");
    land(&mut c, &mut f);
    let r = c.docs["Command:sec"].props["run_result"].clone();
    let chunks: Vec<J> = c.docs.iter().filter(|(k, _)| k.starts_with("Chunk:")).map(|(_, d)| J::Object(d.props.clone())).collect();
    let rows = c.docs.iter().map(|(k, d)| format!("{k} {}", serde_json::to_string(&d.props).unwrap())).collect::<Vec<_>>().join("\n");
    std::env::remove_var(var);
    (r, chunks, rows)
}
/// the secret and every piece of it of 6+ characters (a prefix or a suffix, or any run inside)
fn pieces(secret: &str) -> Vec<String> {
    let b: Vec<char> = secret.chars().collect();
    let mut v = vec![secret.to_string()];
    for len in 6..b.len() { for i in 0..=(b.len() - len) { v.push(b[i..i + len].iter().collect()); } }
    v
}
fn assert_no_piece(what: &str, text: &str) { for p in pieces(SECRET) { assert!(!text.contains(&p), "{what}: holds the piece {p:?} of the secret: {text}"); } }
/// the shell that prints the first `cut` characters of $SK, waits, then the rest
fn split_script(prefix: &str, suffix: &str, cut: usize, fd: &str, gap: &str) -> String {
    format!("a=$(printf %s \"$SK\" | cut -c1-{cut}); b=$(printf %s \"$SK\" | cut -c{}-); {{ printf '{prefix}%s' \"$a\"; sleep 0.4; printf '%s{suffix}' \"$b\"; }}{fd}{gap}", cut + 1)
}

#[test]
fn a_secret_split_across_two_reads_of_a_partial_run_is_in_no_chunk_and_no_receipt_on_stdout_or_stderr() {
    for (name, fd) in [("stdout", ""), ("stderr", " >&2")] {
        let (r, chunks, rows) = run_secret("W47_KEY_SPLIT", SECRET, &split_script("first line\\nkey=", "\\nlast line\\n", 9, fd, ""));
        assert_eq!(r["rc"], json!(0), "{name}: {r}");
        assert_no_piece(&format!("{name} receipt"), &r.to_string());
        for ch in &chunks { assert_no_piece(&format!("{name} chunk"), &ch.to_string()); }
        assert_no_piece(&format!("{name} rows"), &rows);
        let all = if name == "stdout" { r["stdout"].as_str().unwrap() } else { r["stderr"].as_str().unwrap() };
        assert!(all.contains("key=***") && all.contains("last line"), "{name}: the words around it stay: {all}");
        if name == "stdout" { assert!(chunks.iter().any(|c| c["data"].as_str().unwrap().contains("***")), "chunks were pushed and masked: {chunks:?}"); }
    }
}

#[test]
fn a_secret_in_an_unterminated_final_tail_is_masked_in_the_last_chunk() {
    let (r, chunks, rows) = run_secret("W47_KEY_TAIL", SECRET, "printf 'ok\\nkey=%s' \"$SK\"");
    assert_no_piece("receipt", &r.to_string());
    for ch in &chunks { assert_no_piece("chunk", &ch.to_string()); }
    assert_no_piece("rows", &rows);
    assert!(chunks.iter().any(|c| c["data"].as_str().unwrap().ends_with("key=***")), "{chunks:?}");
}

#[test]
fn a_secret_that_straddles_the_end_of_one_16k_read_buffer_is_masked() {
    // 16380 filler characters on the line, then the secret: it begins inside the first 16 KB and ends past it
    let (r, chunks, rows) = run_secret("W47_KEY_BIG", SECRET, "head -c 16380 /dev/zero | tr '\\0' 'x'; printf '%s\\nend\\n' \"$SK\"");
    assert_no_piece("receipt", &r.to_string());
    for ch in &chunks { assert_no_piece("chunk", &ch.to_string()); }
    assert_no_piece("rows", &rows);
    assert!(r["stdout"].as_str().unwrap().contains("x***"), "{}", r["stdout"].as_str().unwrap().len());
}

#[test]
fn a_secret_that_holds_a_newline_is_not_cut_in_two_by_the_chunk_boundary() {
    // a value with a line break in it (a PEM-like secret): the chunk cut at the last newline must not fall inside it
    let multi = "AAAA-first-line-0123\nBBBB-second-line-4567";
    let (r, chunks, _) = run_secret("W47_KEY_NL", multi, "{ printf 'x\\n'; printf '%s' \"$SK\" | head -c 25; sleep 0.4; printf '%s' \"$SK\" | tail -c +26; printf '\\n'; }");
    for ch in &chunks { let t = ch.to_string(); assert!(!t.contains("AAAA-first") && !t.contains("BBBB-second") && !t.contains("second-line"), "chunk: {t}"); }
    assert!(!r.to_string().contains("BBBB-second"), "{r}");
}

#[test]
fn a_handed_over_value_shorter_than_16_characters_is_not_masked_it_is_no_credential() {
    for (value, masked) in [("abcdefghijklmno", false), ("abcdefghijklmnop", true)] {
        let (r, chunks, _) = run_secret("W47_KEY_SHORT", value, "printf 'say %s and %s\\n' \"$SK\" \"$SK\"");
        let out = r["stdout"].as_str().unwrap().to_string();
        assert_eq!(out.contains("***"), masked, "{value}: {out}");
        assert_eq!(out.contains(value), !masked, "{value}: {out}");
        let cd = chunks.iter().map(|c| c["data"].as_str().unwrap().to_string()).collect::<String>();
        assert_eq!(cd.contains("***"), masked, "{value} chunks: {cd}");
    }
}
