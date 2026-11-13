//! The barest seat's help (#1143): `Help:c1f`, `Help:packs` and `Help:upgrade` are entities whose text is the markdown under
//! `membrane/apps/barest.c1f/help/`. Every code block of those files is loaded through the real loader and run through the real
//! evaluator here, so the help cannot lie. A fence ```c1f is a pack that must load; ```c1f-error is a pack the loader must refuse.
//! Directives in the block's `--` comments: `-- given <eid> <json props>`, `-- write <eid>.<prop> = <json>`, `-- reads <eid>.<prop> = <json>`
//! (checked in order, after the pack loaded), `-- error: <text the loader's message holds>`.
use membrane_vm::host::{Host, Options};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
const TOPICS: [&str; 3] = ["c1f", "packs", "upgrade"];

struct Block { topic: String, kind: String, body: String, line: usize }

fn blocks(topic: &str) -> Vec<Block> {
    let text = std::fs::read_to_string(repo().join(format!("membrane/apps/barest.c1f/help/{topic}.md"))).unwrap_or_else(|e| panic!("help/{topic}.md: {e}"));
    let mut out = vec![];
    let mut cur: Option<(String, String, usize)> = None;
    for (i, l) in text.lines().enumerate() {
        match &mut cur {
            None => if let Some(k) = l.strip_prefix("```c1f") { cur = Some((format!("c1f{k}"), String::new(), i + 1)); },
            Some((k, b, at)) => if l.starts_with("```") { out.push(Block { topic: topic.into(), kind: k.clone(), body: b.clone(), line: *at }); cur = None; } else { b.push_str(l); b.push('\n'); },
        }
    }
    assert!(cur.is_none(), "{topic}.md: a fence is not closed");
    out
}

fn host() -> Host {
    let h = Host::open({ let mut o = Options::new(None); o.effects = false; o }).unwrap();
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

#[test]
fn the_doctrine_in_the_chat_turn_points_the_model_at_the_help() {
    let t = std::fs::read_to_string(repo().join("membrane/packs/chat_turn.c1f")).unwrap();
    assert!(t.contains("Help:<topic> entities teach the language: read Help:c1f first"));
    for topic in TOPICS { assert!(t.contains(&format!("Help:{topic}")) || topic == "c1f", "{topic}"); }
}
