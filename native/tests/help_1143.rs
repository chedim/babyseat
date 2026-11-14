//! The barest seat's help (#1143): `Help:c1f`, `Help:packs` and `Help:upgrade` are entities whose text is the markdown under
//! `membrane/apps/barest.c1f/help/`. Every code block of those files is loaded through the real loader and run through the real
//! evaluator here, so the help cannot lie. A fence ```c1f is a pack that must load; ```c1f-error is a pack the loader must refuse.
//! Directives in the block's `--` comments: `-- given <eid> <json props>`, `-- write <eid>.<prop> = <json>`, `-- reads <eid>.<prop> = <json>`
//! (checked in order, after the pack loaded), `-- error: <text the loader's message holds>`.
use membrane_vm::host::{Host, Options};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
const TOPICS: [&str; 7] = ["c1f", "packs", "upgrade", "banner", "cliff", "semtext", "sql"];

struct Block { topic: String, kind: String, body: String, line: usize }

fn blocks(topic: &str) -> Vec<Block> {
    let text = std::fs::read_to_string(repo().join(format!("membrane/apps/barest.c1f/help/{topic}.md"))).unwrap_or_else(|e| panic!("help/{topic}.md: {e}"));
    let mut out = vec![];
    let mut cur: Option<(String, String, usize)> = None;
    for (i, l) in text.lines().enumerate() {
        match &mut cur {
            None => if let Some(k) = l.strip_prefix("```c1f") { cur = Some((format!("c1f{k}"), String::new(), i + 1)); } else if let Some(k) = l.strip_prefix("```cliff") { cur = Some((format!("cliff{k}"), String::new(), i + 1)); },
            Some((k, b, at)) => if l.starts_with("```") { out.push(Block { topic: topic.into(), kind: k.clone(), body: b.clone(), line: *at }); cur = None; } else { b.push_str(l); b.push('\n'); },
        }
    }
    assert!(cur.is_none(), "{topic}.md: a fence is not closed");
    out
}

fn host() -> Host { let mut o = Options::new(None); o.effects = false; host_with(o) }
/// the same host with the real drivers: a store in a folder of its own (the commands run there)
fn host_live(dir: &std::path::Path) -> Host { std::fs::create_dir_all(dir.join(".membrane")).unwrap(); host_with(Options::new(Some(dir.join(".membrane/s.sqlite3").to_string_lossy().to_string()))) }   // the seat's folder is `dir` (a relative path in an example is under it)
fn host_with(o: Options) -> Host {
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap();
    }
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.rebuild();
    h
}

fn same(a: &J, b: &J) -> bool { match (a.as_f64(), b.as_f64()) { (Some(x), Some(y)) => (x - y).abs() < 1e-9, _ => a == b } }
fn target(s: &str) -> (String, String) { let (e, p) = s.rsplit_once('.').unwrap_or_else(|| panic!("no .prop in {s}")); (e.trim().into(), p.trim().into()) }

fn run(b: &Block, n: usize) {
    let at = format!("{}.md line {} ({})", b.topic, b.line, b.kind);
    if b.kind == "c1f-run" { return run_live(b, &at); }
    if b.kind.starts_with("cliff") { return run_cliff(b, &at); }
    let h = host();
    let name = b.body.lines().find_map(|l| l.strip_prefix("PACK ")).and_then(|r| r.split_whitespace().next()).map(String::from).unwrap_or(format!("ex{n}"));
    let res = h.load_pack(&name, &b.body);
    if b.kind == "c1f-error" {
        let want = b.body.lines().find_map(|l| l.strip_prefix("-- error: ")).unwrap_or_else(|| panic!("{at}: a c1f-error block says `-- error: <text>`")).trim();
        let e = res.err().unwrap_or_else(|| panic!("{at}: the loader accepted a pack the help says is refused"));
        assert!(e.contains(want), "{at}: the loader said {e:?}, the help says it holds {want:?}");
        return;
    }
    res.unwrap_or_else(|e| panic!("{at}: the loader refused the example: {e}"));
    h.rebuild();
    for l in b.body.lines() {
        if let Some(r) = l.strip_prefix("-- given ") {
            let (eid, props) = r.split_once(' ').unwrap();
            let cls = eid.split(':').next().unwrap();
            h.born(eid, cls, serde_json::from_str::<J>(props).unwrap().as_object().unwrap().clone(), "person");
            h.pump();
        } else if let Some(r) = l.strip_prefix("-- write ") {
            let (t, v) = r.split_once(" = ").unwrap();
            let (e, p) = target(t);
            h.write(&e, &p, serde_json::from_str(v).unwrap(), "person");
            h.pump();
        } else if let Some(r) = l.strip_prefix("-- reads ") {
            let (t, v) = r.split_once(" = ").unwrap();
            let (e, p) = target(t);
            let want: J = serde_json::from_str(v).unwrap();
            let got = h.get(&e, &p);
            assert!(same(&got, &want), "{at}: {e}.{p} reads {got}, the help says {want}");
        }
    }
}

/// A ```cliff block is a clifflet (the `run_cliff` tool, packs/door_tools.c1f): its non-directive lines are loaded the way the door loads them, as the body of a getter
/// (`cliff`, the value is read) or of an on-setter (`cliff-write`, the writes run). Directives: `-- given <eid> <json>`, `-- value <json>` (the value), `-- text <text>` (its printout, `str` of the value), `-- error: <text>`
/// (the loader's message or the null's reason holds the text), `-- reads <eid>.<prop> = <json>` (after a `cliff-write` ran).
fn run_cliff(b: &Block, at: &str) {
    let h = host();
    let code: String = b.body.lines().filter(|l| !l.starts_with("-- ")).collect::<Vec<_>>().join("\n");
    let indent = |n: usize| code.lines().map(|l| format!("{}{l}\n", " ".repeat(n))).collect::<String>();
    let write = b.kind == "cliff-write";
    for l in b.body.lines() {
        if let Some(r) = l.strip_prefix("-- given ") {
            let (eid, props) = r.split_once(' ').unwrap();
            h.born(eid, eid.split(':').next().unwrap(), serde_json::from_str::<J>(props).unwrap().as_object().unwrap().clone(), "person");
            h.pump();
        }
    }
    let want_err = b.body.lines().find_map(|l| l.strip_prefix("-- error: ").or_else(|| l.strip_prefix("-- null: "))).map(str::trim);   // `-- null: <reason>` is how run_cliff prints a null with a reason
    let text = if write { format!("PACK cliffx 1\nCliffx.go on-setter run:\n  ALWAYS:\n{}", indent(4)) } else { format!("PACK cliffx 1\nCliffx.val getter:\n{}Cliffx.shown getter: {{'v': this.val, 'why': why(this.val), 'text': str(this.val)}}\n", indent(2)) };
    let loaded = h.load_pack("cliffx", &text);
    if let Some(w) = want_err {
        if let Err(e) = &loaded { assert!(e.contains(w), "{at}: the loader said {e:?}, the help says it holds {w:?}"); return; }
    }
    loaded.unwrap_or_else(|e| panic!("{at}: the loader refused the example: {e}"));
    h.write("Pack:cliffx", "scratch", J::Bool(true), "person");   // the door marks its scratch packs so `instances` does not list them
    h.pump();
    h.rebuild();
    if write {
        h.write("Pack:cliffx", "privileged", J::Bool(true), "person");   // the door grants its scratch setter pack the privilege, so a block may write any class
        h.pump();
        h.born("Cliffx:x", "Cliffx", Default::default(), "person");
        h.write("Cliffx:x", "go", J::Bool(true), "person");
        h.pump();
        for l in b.body.lines() {
            if let Some(r) = l.strip_prefix("-- reads ") {
                let (t, v) = r.split_once(" = ").unwrap();
                let (e, p) = target(t);
                let want: J = serde_json::from_str(v).unwrap();
                assert!(same(&h.get(&e, &p), &want), "{at}: {e}.{p} reads {}, the help says {want} (refused: {}, {})", h.get(&e, &p), h.get(&e, "refused"), h.get("Cliffx:x", "refused"));
            }
        }
        return;
    }
    h.born("Cliffx:x", "Cliffx", Default::default(), "person");
    h.pump();
    let shown = h.get("Cliffx:x", "shown");
    if let Some(w) = want_err {
        let why = shown["why"].as_str().unwrap_or("");
        assert!(why.contains(w), "{at}: the null's reason is {why:?}, the help says it holds {w:?}");
        return;
    }
    if let Some(t) = b.body.lines().find_map(|l| l.strip_prefix("-- text ")) {
        assert_eq!(shown["text"].as_str().unwrap_or(""), t.trim(), "{at}: the printout of the clifflet is not what the help says");
        return;
    }
    let want: J = serde_json::from_str(b.body.lines().find_map(|l| l.strip_prefix("-- value ")).unwrap_or_else(|| panic!("{at}: a cliff block says `-- value <json>` or `-- error: <text>`"))).unwrap();
    assert!(same(&shown["v"], &want), "{at}: the clifflet returns {}, the help says {want} (why: {})", shown["v"], shown["why"]);
}

/// A ```c1f-run block is RUN through the real drivers: `-- write <eid>.<prop> = <json>` writes (a write of `run` waits for that run's receipt, matched by its id),
/// `-- receipt <eid>.<prop> = <json>` reads a property of the receipt (`run_result.stdout`) and compares it.
fn run_live(b: &Block, at: &str) {
    let dir = std::env::temp_dir().join(format!("help-live-{}-{}", std::process::id(), b.line));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let h = host_live(&dir);
    for l in b.body.lines() {
        if let Some(r) = l.strip_prefix("-- sqlite ") {      // `-- sqlite <file> = <SQL script>`: a SQLite file in the seat's folder, made before the example runs
            let (f, sql) = r.split_once(" = ").unwrap();
            rusqlite::Connection::open(dir.join(f.trim())).unwrap().execute_batch(sql).unwrap();
        } else if let Some(r) = l.strip_prefix("-- reads ") {
            let (t, v) = r.split_once(" = ").unwrap();
            let (e, p) = target(t);
            let want: J = serde_json::from_str(v).unwrap();
            let got = h.get(&e, &p);
            assert!(same(&got, &want), "{at}: {e}.{p} reads {got}, the help says {want}");
        } else if let Some(r) = l.strip_prefix("-- write ") {
            let (t, v) = r.split_once(" = ").unwrap();
            let (e, p) = target(t);
            let val: J = serde_json::from_str(v).unwrap();
            if !h.snapshot().contains_key(&e) { h.born(&e, e.split(':').next().unwrap(), Default::default(), "person"); }   // a write makes the entity, as the door does
            h.write(&e, &p, val.clone(), "person");
            if p == "run" {
                let id = val.get("id").cloned().unwrap_or(J::Null);
                let t0 = std::time::Instant::now();
                loop {
                    h.pump();
                    let got = h.get(&e, "run_result");
                    if got.get("id") == Some(&id) { break; }
                    assert!(t0.elapsed() < std::time::Duration::from_secs(20), "{at}: no receipt for run id {id} on {e} (the example does not run); run_result is {got}");
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
            } else { h.pump(); }
        } else if let Some(r) = l.strip_prefix("-- receipt ") {
            let (t, v) = r.split_once(" = ").unwrap();
            let (path, field) = t.rsplit_once('.').unwrap();
            let (e, p) = path.rsplit_once('.').unwrap();
            let got = h.get(e.trim(), p.trim()).get(field.trim()).cloned().unwrap_or(J::Null);
            let want: J = serde_json::from_str(v).unwrap();
            assert!(same(&got, &want), "{at}: {e}.{p}.{field} reads {got}, the help says {want}");
        }
    }
    drop(h);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_command_in_the_help_names_its_cmd_before_it_runs_and_runs_for_real() {
    let mut runs = 0;
    for t in TOPICS {
        for b in blocks(t) {
            if b.kind == "c1f-run" { runs += 1; continue; }
            // a pack that writes `Command:<name>.run` also writes `Command:<name>.cmd` (the driver answers 'a command needs `cmd`' otherwise)
            for l in b.body.lines().filter(|l| l.contains(".run = ") && l.contains("Command:")) {
                let name = l.trim().split(".run").next().unwrap().to_string();
                assert!(b.body.contains(&format!("{name}.cmd = ")), "{}.md line {}: `{l}` runs a command that was never given a cmd", t, b.line);
            }
        }
    }
    assert!(runs >= 1, "the help shows a Command example that is run through the real driver (a ```c1f-run block)");
}

#[test]
fn every_example_in_the_help_loads_and_reads_as_the_help_says() {
    let mut n = 0;
    let mut seen = 0;
    for t in TOPICS {
        for b in blocks(t) { n += 1; seen += 1; run(&b, n); }
    }
    assert!(seen >= 20, "the help carries its examples: {seen}");
}

#[test]
fn the_help_states_the_default_operator_and_that_double_question_mark_is_refused() {
    let c = std::fs::read_to_string(repo().join("membrane/apps/barest.c1f/help/c1f.md")).unwrap();
    assert!(c.contains("A default is `or`") && c.contains("`??`") && c.contains("unexpected '?'"));
    // the claim is the loader's own message
    let h = host();
    let e = h.load_pack("q", "Box.label getter: this.note ?? 'x'\n").err().unwrap();
    assert!(e.contains("unexpected '?'"), "{e}");
}

#[test]
fn the_help_is_positive_and_domain_neutral() {
    for t in TOPICS {
        let s = std::fs::read_to_string(repo().join(format!("membrane/apps/barest.c1f/help/{t}.md"))).unwrap().to_lowercase();
        for bad in ["no history", "cannot", "does not have", "doesn't have", "babyseat", "membrane"] { assert!(!s.contains(bad), "{t}.md says {bad:?}"); }
    }
}

const DOCTRINE: &str = "Modify your banner to help you maintain continuity. Keep it fresh: when you see junk or outdated information in it, remove it. Use Help entities for help. Program the graph as your memory. Process updates on the graph and react to them.";

#[test]
fn the_doctrine_in_the_chat_turn_is_the_operators_four_sentences_and_the_help_tells_where_it_lives() {
    let t = std::fs::read_to_string(repo().join("membrane/packs/chat_turn.c1f")).unwrap();
    let at = t.lines().position(|l| l.starts_with("ChatMessage.note getter:")).unwrap();
    let note = t.lines().nth(at + 2).unwrap();   // the second line: `WHEN this.over: None` guards it (a finished message holds no prompt props), then the sentences
    assert_eq!(note, format!("  ELSE: '{DOCTRINE}'"), "the note is exactly the four sentences");
    assert!(!note.contains("YOUR BANNER IS") && !note.contains("Good luck"), "the old long doctrine is gone: {note}");
    // the static instructions live in the help: where Help is, and how the banner is replaced
    let c = std::fs::read_to_string(repo().join("membrane/apps/barest.c1f/help/c1f.md")).unwrap();
    assert!(c.contains("entity_query `kind:Help`") && c.contains("Help:banner") && c.contains("Help:packs"), "Help:c1f lists the topics and how to list them");
    let b = std::fs::read_to_string(repo().join("membrane/apps/barest.c1f/help/banner.md")).unwrap();
    assert!(b.contains("Banner.text") && b.contains("default_banner"), "Help:banner teaches the replacement");
}

#[test]
fn the_help_says_what_num_json_and_a_null_with_a_reason_do() {
    // operator decisions 2026-11-13: Num is a value kind, json(bad text) is an Error, run_cliff prints `null: <reason>` (not an error)
    let rd = |t: &str| std::fs::read_to_string(repo().join(format!("membrane/apps/barest.c1f/help/{t}.md"))).unwrap();
    let (c, u, l) = (rd("c1f"), rd("upgrade"), rd("cliff"));
    assert!(c.contains("`x is Num`") && c.contains("`json(text)`") && c.contains("is an Error"), "Help:c1f");
    assert!(u.contains("`Int Float Num") && u.contains("`null: <reason>`") && u.contains("`json(text)` of text that is not JSON is an Error"), "Help:upgrade");
    assert!(l.contains("`null: <the reason>`") && !l.contains("`error: <the reason>`"), "Help:cliff");
}
