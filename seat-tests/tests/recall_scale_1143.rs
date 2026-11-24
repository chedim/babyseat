//! THE RECALL SCALES (#1143, operator 2026-11-15): the parts of `SemText.recall` that read long lists hand them to builtins that cost one step each (`props`, `vmean`,
//! `group_by` with sums), so their steps do not grow with the mentions they read. Measured here by the engine's own accounting (`stats`): the steps of the candidates'
//! means in entity recall (`entityrecall.c1f`) and of the edge lane (`mentions.c1f` `r_edge`) on a field where three words are in every text.
use cliffvm::host::{boot_started, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }

const MAKER: &str = "PACK rsmaker 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT chat 1\n\
Box.say on-setter say:\n  WHEN this.say != None:\n    membrane::Chat:main:message[${this.say}['id']]:body = SemText(text: ${this.say}['text'])\n    membrane::Chat:main:message[${this.say}['id']]:body.mention = True\n";

fn seat() -> Host {
    cliffvm::expr::set_eval_time_ms(Some(10_000));
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    let r = repo();
    let list = std::fs::read_to_string(r.join("membrane/apps/barest.packs")).unwrap();
    for (i, n) in list.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).enumerate() {
        h.load_app(&r.join(format!("membrane/packs/{n}.c1f")), i == 0).unwrap_or_else(|e| panic!("{n}: {e}"));
    }
    h.load_app(&r.join("membrane/packs/launcher.c1f"), false).unwrap();
    h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    h.load_pack("rsmaker", MAKER).unwrap();
    h.rebuild();
    let mut f = Map::new();
    f.insert("seat_backend".into(), json!("http://127.0.0.1:9"));
    f.insert("embed_model".into(), json!("m"));
    boot_started(&h, &f, true);
    h.pump();
    h.born("Box:a", "Box", Map::new(), "person");
    h.pump();
    h
}
const Q: &str = "membrane::Embed:queue";
fn drain(h: &Host) {
    for _ in 0..80 {
        let j = h.get(Q, "inflight");
        if j.is_null() || h.get(Q, "flying") != json!(true) { break; }
        let ins: Vec<String> = j["unc"].as_array().unwrap().iter().map(|x| x["text"].as_str().unwrap().to_string()).collect();
        if ins.is_empty() { break; }
        let embeddings: Vec<Vec<f64>> = ins.iter().map(|t| hashed(t)).collect();
        h.write("Fetch:embed", "request_result", json!({"id": j["id"], "status": "ok", "rc": 0, "head": "HTTP/1.1 200 OK", "http_status": 200, "headers": {}, "body": json!({"model": "m", "embeddings": embeddings}).to_string()}), "driver:request");
        h.pump();
    }
}
/// a vector of 16 numbers from the text (FNV-1a), the same text the same vector
fn hashed(t: &str) -> Vec<f64> {
    let mut x: u64 = 0xcbf29ce484222325;
    for b in t.bytes() { x ^= b as u64; x = x.wrapping_mul(0x100000001b3); }
    (0..16u32).map(|i| { let y = x.rotate_left(i * 4) ^ (i as u64).wrapping_mul(0x9e3779b97f4a7c15); ((y % 2000) as f64) / 1000.0 - 1.0 }).collect()
}

/// a chat of `n` texts, each naming 8 of 40 identifiers and the three words every text says (`item`, `uses`, `with`), and a question naming some of them;
/// the steps each SemText getter took in one recall of the question
fn steps_of_one_recall(n: usize) -> (Host, Vec<(String, u64)>) {
    let h = seat();
    let say = |id: &str, text: &str| { h.write("Box:a", "say", json!({"id": id, "text": text}), "person"); h.pump(); };
    for i in 0..n {
        let ids: Vec<String> = (0..8).map(|j| format!("id{}_x", (i * 3 + j * 5) % 40)).collect();
        say(&format!("m{i:04}"), &format!("Item {i} uses {} with u_{i}x.", ids.join(" ")));
        drain(&h);
    }
    say("q", "Which item uses id0_x id5_x id10_x id15_x id20_x with u_7x?");
    drain(&h);
    let e = "membrane::Chat:main:message:q:body";
    cliffvm::stats::set_on(true);
    cliffvm::stats::global_clear();
    h.write(e, "recall_on", json!(true), "person"); h.pump();
    let rows = cliffvm::stats::global_lifetime().into_iter().filter(|r| r.cls == "SemText" && r.kind == "getter").map(|r| (r.prop, r.steps)).collect();
    cliffvm::stats::set_on(false);
    (h, rows)
}
fn sum(rows: &[(String, u64)], pick: &dyn Fn(&str) -> bool) -> u64 { rows.iter().filter(|(p, _)| pick(p)).map(|(_, s)| s).sum() }

#[test]
fn the_candidates_means_and_the_edge_lane_cost_steps_per_row_not_per_mention() {
    let (h, rows) = steps_of_one_recall(120);
    let e = "membrane::Chat:main:message:q:body";
    // (`r_older` fails here: this seat has no store, so the window is the ask alone)
    let failed = h.get(e, "failed");
    assert!(failed.as_object().map(|m| m.keys().all(|k| k == "r_older")).unwrap_or(failed == J::Null), "no evaluation of the recall failed: {failed}");
    assert_eq!(h.get(e, "entity_hits").as_array().map(|a| a.len()), Some(8), "entity recall voted");
    assert_eq!(h.get(e, "r_edge_rows").as_array().map(|a| a.len()), Some(24), "the edge lane is full");
    // 120 texts: the three common words alone have 360 mentions among the candidates; read item by item that is tens of steps a mention
    let means = sum(&rows, &|p| ["ent_mrows", "ent_mvecs", "ent_means"].contains(&p));
    assert!(means < 3_000, "the candidates' means take {means} steps: {rows:?}");
    let edge = sum(&rows, &|p| p == "r_edge");
    eprintln!("RECALL_SCALE 120 texts: the means {means} steps, the edge lane's grouping {edge} steps");
    assert!(edge < 3_000, "the edge lane's grouping takes {edge} steps: {rows:?}");
}
