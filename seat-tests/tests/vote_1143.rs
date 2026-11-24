//! THE VOTE (#1143, step 6 of the field on SemText; membrane/packs/mentions.c1f): `SemText.recall` ranks its hits by the old seat's vote (`recall_vote.py`), not by
//! reciprocal rank. Each voter's top DEPTH (20) rows are a ballot; the i-th row (from 0) of a voter earns that voter's weight x (DEPTH - i); the points are summed per
//! key (the text's eid + '#' + the sentence's start), best first, ties by key. The voters and their weights (operator 2026-11-11, kept with voters missing): the move log
//! (`movelog_votes`, 2), entity recall (`entity_votes`, 0.75), the embedder = the cosine lane (0.125), the word lane = the edge lane (0.125). The lanes' rows past the depth
//! stay hits with no points (no cut-off); the ask is never a row. The rerank of the first `rerank_top` reads the `legacy` weight set.
//! The ported scenarios of the old `tests/test_recall_vote_0752.py`: Borda by rank, a missing voter, ties by key, an item one voter finds, the move log outranks, the sum
//! across voters, no cut-off, the ask never a row. The voters are stubbed by a layer (`votestub`) so the points are exact; one test runs the real voters.
use cliffvm::host::{self, Host, Options};
use serde_json::{json, Map, Value as J};
use std::path::PathBuf;

fn repo() -> PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn props(v: J) -> Map<String, J> { v.as_object().unwrap().clone() }

/// `Leaf.go` makes a text that is not the chat's; `Box.say` a mentioned chat body
const MAKER: &str = "PACK vtmaker 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT chat 1\n\
Leaf.go on-setter leaf:\n  WHEN this.go != None:\n    Leaf:x[this.k]:body = SemText(text: this.go)\n    Leaf:x[this.k]:body.mention = True\n\
Box.say on-setter say:\n  WHEN this.say != None:\n    membrane::Chat:main:message[${this.say}['id']]:body = SemText(text: ${this.say}['text'])\n    membrane::Chat:main:message[${this.say}['id']]:body.mention = True\n";

/// a model's layer that replaces the four voters by the rows of `Stub:s` while `on` (a voter `'fail'` is an Error, a voter not given is absent)
const STUB: &str = "PACK votestub 1\nIMPORT semtext 1\nIMPORT mentions 1\nIMPORT movelog 1\nIMPORT entityrecall 1\n\
Stub.kind getter: 'stub'\n\
SemText.movelog_votes getter WHEN ${'Stub:s'}['on'] == True:\n  Error('the voter failed') if ${'Stub:s'}['ml'] == 'fail' else ${'Stub:s'}['ml']\n\
SemText.entity_votes getter WHEN ${'Stub:s'}['on'] == True:\n  Error('the voter failed') if ${'Stub:s'}['er'] == 'fail' else ${'Stub:s'}['er']\n\
SemText.r_edge_rows getter WHEN ${'Stub:s'}['on'] == True:\n  ${'Stub:s'}['edge'] or []\n\
SemText.r_cos_rows getter WHEN ${'Stub:s'}['on'] == True:\n  ${'Stub:s'}['cos'] or []\n";

fn seat_with(layers: &[&str]) -> Host {
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
    let l = h.load_app(&r.join("membrane/apps/barest.c1f"), false).unwrap();
    assert!(l.msgs.is_empty(), "the app loads clean: {:?}", l.msgs);
    h.load_pack("vtmaker", MAKER).unwrap();
    for l in layers { let name = l.lines().next().unwrap().split_whitespace().nth(1).unwrap(); h.load_pack(name, l).unwrap(); }
    h.rebuild();
    let mut f = Map::new();
    f.insert("chat_claude_bin".into(), json!("/usr/bin/true")); f.insert("seat_default".into(), json!("claude"));
    f.insert("chat_auth".into(), json!("login"));
    f.insert("seat_backend".into(), json!("http://127.0.0.1:9"));
    f.insert("embed_model".into(), json!("m"));
    host::boot_started(&h, &f, true);
    h.pump();
    h.born("Box:a", "Box", Map::new(), "person");
    h.pump();
    h
}
/// a seat whose voters are the stub's
fn stubbed() -> Host { stubbed_with(&[]) }
fn stubbed_with(more: &[&str]) -> Host {
    let mut ls = vec![STUB];
    ls.extend_from_slice(more);
    let h = seat_with(&ls);
    h.born("Stub:s", "Stub", Map::new(), "person"); h.pump();
    h
}
const ASK: &str = "Leaf:x:q:body";
fn leaf(h: &Host, k: &str, text: &str) {
    h.born(&format!("Leaf:{k}"), "Leaf", props(json!({"k": k})), "person"); h.pump();
    h.write(&format!("Leaf:{k}"), "go", json!(text), "person"); h.pump();
}
/// a voter's row (the move log's and entity recall's shape)
fn row(k: &str, text: &str) -> J { json!({"key": format!("Leaf:x:{k}:body#0"), "of": format!("Leaf:x:{k}:body"), "text": text, "score": 1.0}) }
/// a lane's row (the edge and the cosine lane's shape: a 1-based rank)
fn lane(k: &str, text: &str, rank: u32) -> J { json!({"key": format!("Leaf:x:{k}:body#0"), "of": format!("Leaf:x:{k}:body"), "text": text, "rank": rank, "sim": 0, "ov": 0}) }
/// set the voters (`ml`, `er`, `edge`, `cos`; a missing one is not written) and read the recall of the ask
fn vote(h: &Host, voters: J) -> Vec<J> {
    for (k, v) in voters.as_object().unwrap() { h.write("Stub:s", k, v.clone(), "person"); }
    h.write("Stub:s", "on", json!(true), "person"); h.pump();
    leaf(h, "q", "Anything at all?");
    h.write(ASK, "recall_on", json!(true), "person"); h.pump();
    let r = h.get(ASK, "recall");
    r.as_array().unwrap_or_else(|| panic!("no recall: {r} ({})", h.get(ASK, "failed"))).clone()
}
fn texts(rows: &[J]) -> Vec<String> { rows.iter().map(|r| r["text"].as_str().unwrap_or("").to_string()).collect() }
fn score(rows: &[J], text: &str) -> f64 { rows.iter().find(|r| r["text"] == json!(text)).unwrap_or_else(|| panic!("{text} not in {rows:?}"))["score"].as_f64().unwrap() }
fn near(a: f64, b: f64, what: &str) { assert!((a - b).abs() < 1e-6, "{what}: {a} vs {b}"); }

/// the old `test_points_order_and_ties_as_the_bench`: each voter's i-th row earns weight x (20 - i), and a key's points are summed across the voters
#[test]
fn each_voters_ith_row_earns_its_weight_times_depth_minus_i_summed_across_voters() {
    let h = stubbed();
    let r = vote(&h, json!({
        "ml": [row("a", "Alpha one."), row("b", "Bravo two.")],
        "er": [row("b", "Bravo two."), row("c", "Charlie three.")],
        "cos": [lane("c", "Charlie three.", 1), lane("d", "Delta four.", 2)],
        "edge": [lane("d", "Delta four.", 1)]}));
    assert_eq!(texts(&r), vec!["Bravo two.", "Alpha one.", "Charlie three.", "Delta four."], "{r:?}");
    near(score(&r, "Alpha one."), 2.0 * 20.0, "the move log's first");
    near(score(&r, "Bravo two."), 2.0 * 19.0 + 0.75 * 20.0, "the move log's second + entity recall's first");
    near(score(&r, "Charlie three."), 0.75 * 19.0 + 0.125 * 20.0, "entity recall's second + the cosine lane's first");
    near(score(&r, "Delta four."), 0.125 * 19.0 + 0.125 * 20.0, "the cosine lane's second + the edge lane's first");
}

/// the move log (weight 2) outranks entity recall (0.75) and the lanes (0.125 each): its first row is the recall's first
#[test]
fn the_move_log_voter_outranks_the_others() {
    let h = stubbed();
    let r = vote(&h, json!({
        "er": [row("e", "Echo five."), row("m", "Mike six.")],
        "cos": [lane("e", "Echo five.", 1)],
        "edge": [lane("e", "Echo five.", 1)],
        "ml": [row("m", "Mike six.")]}));
    assert_eq!(texts(&r)[0], "Mike six.", "40 + 14.25 against 15 + 2.5 + 2.5: {r:?}");
}

/// the old `test_a_voter_that_fails_never_breaks_recall`: a voter that is not there or that failed adds nothing; the others still vote with their own weights
#[test]
fn a_missing_or_failed_voter_contributes_nothing() {
    let h = stubbed();
    let r = vote(&h, json!({"ml": "fail", "cos": [lane("c", "Charlie three.", 1)], "edge": [lane("d", "Delta four.", 1), lane("c", "Charlie three.", 2)]}));
    assert_eq!(texts(&r), vec!["Charlie three.", "Delta four."], "{r:?}");
    near(score(&r, "Charlie three."), 0.125 * 20.0 + 0.125 * 19.0, "the weights stay the approved ones with voters missing");
    near(score(&r, "Delta four."), 0.125 * 20.0, "edge");
}

/// equal points: the key decides, not the order the voters or their rows came in
#[test]
fn ties_go_by_key() {
    let h = stubbed();
    // the edge lane's rows come first in the ballots: a first-seen order would put Zulu first
    let r = vote(&h, json!({"edge": [lane("z", "Zulu last.", 1)], "cos": [lane("b", "Bravo two.", 1)], "er": [], "ml": []}));
    assert_eq!(texts(&r), vec!["Bravo two.", "Zulu last."], "{r:?}");
    near(score(&r, "Bravo two."), score(&r, "Zulu last."), "a tie");
}

/// an item only one voter finds is still a hit (entity recall and the move log bring texts the lanes never saw)
#[test]
fn an_item_only_one_voter_finds_still_appears() {
    let h = stubbed();
    let r = vote(&h, json!({"er": [row("o", "Oscar only.")], "cos": [lane("c", "Charlie three.", 1)]}));
    assert!(texts(&r).contains(&"Oscar only.".to_string()), "{r:?}");
    near(score(&r, "Oscar only."), 0.75 * 20.0, "one voter's first");
}

/// only the first DEPTH (20) rows of a voter earn points; a lane's rows past the depth stay hits with 0 points (no cut-off), after the voted ones, by key
#[test]
fn only_the_first_depth_rows_earn_points_and_nothing_is_cut() {
    let h = stubbed();
    let cos: Vec<J> = (0..23).map(|i| lane(&format!("c{i:02}"), &format!("Cosine{i} row{i} here{i}."), i + 1)).collect();
    let ml: Vec<J> = (0..22).map(|i| row(&format!("m{i:02}"), &format!("Moved{i} row{i} there{i}."))).collect();
    let edge: Vec<J> = (0..22).map(|i| lane(&format!("e{i:02}"), &format!("Edge{i} row{i} near{i}."), i + 1)).collect();
    let r = vote(&h, json!({"cos": cos, "ml": ml, "edge": edge}));
    assert_eq!(r.len(), 23 + 22 + 20, "every row of both lanes, and the move log's first 20: {r:?}");
    near(score(&r, "Edge21 row21 near21."), 0.0, "the edge lane's 22nd is a hit too");
    assert!(!texts(&r).contains(&"Moved20 row20 there20.".to_string()), "a voter's ballot is its top 20: {r:?}");
    near(score(&r, "Cosine19 row19 here19."), 0.125, "the 20th earns 1 x the weight");
    near(score(&r, "Cosine20 row20 here20."), 0.0, "the 21st earns nothing");
    near(score(&r, "Cosine22 row22 here22."), 0.0, "and is still a hit");
    let n = r.len();
    assert_eq!(texts(&r)[n - 5..].to_vec(), vec!["Cosine20 row20 here20.", "Cosine21 row21 here21.", "Cosine22 row22 here22.", "Edge20 row20 near20.", "Edge21 row21 near21."], "after the voted ones, by key: {r:?}");
}

/// the ask itself is never a row, even when a voter names it
#[test]
fn the_ask_is_never_a_row() {
    let h = stubbed();
    let me = json!({"key": format!("{ASK}#0"), "of": ASK, "text": "Anything at all?", "score": 1.0});
    let r = vote(&h, json!({"ml": [me.clone(), row("a", "Alpha one.")], "er": [me], "cos": [lane("a", "Alpha one.", 1)]}));
    assert_eq!(texts(&r), vec!["Alpha one."], "{r:?}");
    near(score(&r, "Alpha one."), 2.0 * 19.0 + 0.125 * 20.0, "the ask still took the move log's first place");
}

/// the depth and the weights are getters a layer overrides (the move log's `ml_weight` 2 is its pack's, entity recall's `ent_weight` 0.75 its pack's)
#[test]
fn the_depth_and_the_weights_are_getters_a_layer_overrides() {
    let h = stubbed();
    let r = vote(&h, json!({"ml": [row("a", "Alpha one.")], "er": [row("b", "Bravo two.")], "cos": [lane("c", "Charlie three.", 1)], "edge": [lane("d", "Delta four.", 1)]}));
    assert_eq!(h.get(ASK, "vote_depth"), json!(20));
    assert_eq!(h.get(ASK, "ml_weight"), json!(2));
    assert_eq!(h.get(ASK, "ent_weight"), json!(0.75));
    assert_eq!(h.get(ASK, "r_cos_weight"), json!(0.125));
    assert_eq!(h.get(ASK, "r_edge_weight"), json!(0.125));
    near(score(&r, "Delta four."), 2.5, "edge");
    let h = stubbed_with(&["PACK vtw 1\nIMPORT semtext 1\nIMPORT mentions 1\nSemText.r_edge_weight getter: 3\nSemText.vote_depth getter: 10\n"]);
    let r = vote(&h, json!({"ml": [row("a", "Alpha one.")], "edge": [lane("d", "Delta four.", 1)]}));
    near(score(&r, "Delta four."), 30.0, "a layer's weight and depth");
    near(score(&r, "Alpha one."), 20.0, "the move log's first at depth 10");
    assert_eq!(texts(&r)[0], "Delta four.");
}

/// the rerank of the first `rerank_top` rows reads the `legacy` weight set (fitted with neither the reply features nor `bge`, the closest to what this seat has)
#[test]
fn the_rerank_reads_the_legacy_weight_set() {
    let h = stubbed();
    vote(&h, json!({"cos": [lane("c", "Charlie three.", 1)]}));
    assert_eq!(h.get(ASK, "r_rw"), h.get("recall:dials", "weights")["legacy"], "{}", h.get(ASK, "r_rw"));
    assert!(h.get(ASK, "r_rw")["bias"].is_number());
}

/// with nothing stubbed, the shipped voters vote: a text that shares no word with the ask is found by its vector, and the recall's rows are the lanes' rows with their points
#[test]
fn the_shipped_lanes_vote_with_their_weights() {
    let h = seat_with(&[]);
    leaf(&h, "a", "Quokkas eat grass.");
    leaf(&h, "b", "Quokkas sleep.");
    leaf(&h, "q", "quokka");
    h.write(ASK, "recall_on", json!(true), "person"); h.pump();
    let r = h.get(ASK, "recall");
    let r = r.as_array().unwrap_or_else(|| panic!("{r}")).clone();
    assert_eq!(texts(&r), vec!["Quokkas eat grass.", "Quokkas sleep."], "the edge lane alone, ties by key: {r:?}");
    near(score(&r, "Quokkas eat grass."), 0.125 * 20.0, "the edge lane's first");
    near(score(&r, "Quokkas sleep."), 0.125 * 19.0, "the edge lane's second");
}

/// The largest the vote can make the hits: both lanes full (`entity_pool_max` 24, `pool` 50) and both voters' ballots full (20 each), no key shared, every hit a sentence of 13 words:
/// the vote, the rerank and the merge of near duplicates (every hit against every hit placed before it) stay inside the step budget (no evaluation fails). VOTE_BISECT=1 prints the
/// largest evaluation's steps.
#[test]
fn full_ballots_of_long_sentences_stay_inside_the_step_budget() {
    let h = stubbed();
    let t = |tag: &str, i: usize| format!("Quokka {} ends here.", (0..10).map(|j| format!("{tag}{i}x{j}")).collect::<Vec<_>>().join(" "));
    let edge: Vec<J> = (0..24).map(|i| lane(&format!("e{i:02}"), &t("e", i), i as u32 + 1)).collect();
    let cos: Vec<J> = (0..50).map(|i| lane(&format!("c{i:02}"), &t("c", i), i as u32 + 1)).collect();
    let ml: Vec<J> = (0..20).map(|i| row(&format!("m{i:02}"), &t("m", i))).collect();
    let er: Vec<J> = (0..20).map(|i| row(&format!("r{i:02}"), &t("r", i))).collect();
    let r = vote(&h, json!({"edge": edge, "cos": cos, "ml": ml, "er": er}));
    assert_eq!(h.get(ASK, "failed"), J::Null, "no evaluation failed");
    assert_eq!(r.len(), 114, "every hit");
    if std::env::var("VOTE_BISECT").is_ok() {
        let ok = |b: u64| -> bool {
            cliffvm::expr::set_eval_budget(Some(b));
            h.write(ASK, "recall_on", json!(false), "person"); h.pump();
            h.write(ASK, "recall_on", json!(true), "person"); h.pump();
            let good = h.get(ASK, "failed").is_null() && h.get(ASK, "recall").as_array().map(|a| a.len()) == Some(114);
            cliffvm::expr::set_eval_budget(None);
            good
        };
        let (mut lo, mut hi) = (1u64, 1_000_000u64);
        while hi - lo > 50 { let mid = (lo + hi) / 2; if ok(mid) { hi = mid } else { lo = mid } }
        cliffvm::expr::set_eval_budget(Some(lo));
        h.write(ASK, "recall_on", json!(false), "person"); h.pump();
        h.write(ASK, "recall_on", json!(true), "person"); h.pump();
        eprintln!("VOTE_BISECT 114 hits: the largest evaluation takes about {hi} steps; under it fails: {}", h.get(ASK, "failed"));
        cliffvm::expr::set_eval_budget(None);
    }
}

// ---- the step budget of the whole recall on a realistic field (measured by hand) -------------------------------------------------------------
const Q: &str = "membrane::Embed:queue";
/// answer every request of the embed queue until it is idle; `f` is the model
fn drain(h: &Host, f: &dyn Fn(&str) -> Vec<f64>) {
    for _ in 0..80 {
        let j = h.get(Q, "inflight");
        if j.is_null() || h.get(Q, "flying") != json!(true) { break; }
        let ins: Vec<String> = j["unc"].as_array().unwrap().iter().map(|x| x["text"].as_str().unwrap().to_string()).collect();
        if ins.is_empty() { break; }
        let embeddings: Vec<Vec<f64>> = ins.iter().map(|t| f(t)).collect();
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
/// the getters of each part of the recall, by name
fn part(k: &str) -> &'static str {
    if k.starts_with("r_edge") { "edge lane" }
    else if k.starts_with("r_cos") || k == "r_qrows" || k == "r_query" { "cosine lane" }
    else if k.starts_with("ml_") || k == "movelog_votes" { "move log" }
    else if k.starts_with("ent_") || k.starts_with("entity_") { "entity recall" }
    else if ["r_ballots", "r_voted", "r_ranked", "r_words", "r_pairs", "r_kept", "recall", "r_rw", "r_rn"].contains(&k) { "vote, rerank, merge" }
    else { "other" }
}

/// THE MEASUREMENT (run by hand: `VOTE_TEXTS=150 cargo test --release --no-default-features --features bin --test vote_1143 measure -- --ignored --nocapture`): a chat of
/// VOTE_TEXTS bodies, each one sentence naming 8 of 40 identifiers, three common words (`item`, `uses`, `with`: every text says them) and an identifier of its own; the
/// question names five of the identifiers, the common words and one text's own identifier. Every voter votes. The smallest step budget under which no evaluation of the
/// recall fails is found by bisecting the thread's budget (each evaluation has its own budget, so this is the largest single evaluation), and the same for each part.
#[test]
#[ignore]
fn measure_the_step_budget_of_the_whole_recall() {
    let n: usize = std::env::var("VOTE_TEXTS").ok().and_then(|v| v.parse().ok()).unwrap_or(150);
    let h = seat_with(&[]);
    let say = |id: &str, text: &str| { h.write("Box:a", "say", json!({"id": id, "text": text}), "person"); h.pump(); };
    for i in 0..n {
        let ids: Vec<String> = (0..8).map(|j| format!("id{}_x", (i * 3 + j * 5) % 40)).collect();
        say(&format!("m{i:04}"), &format!("Item {i} uses {} with u_{i}x.", ids.join(" ")));
        drain(&h, &hashed);
    }
    say("q", "Which item uses id0_x id5_x id10_x id15_x id20_x with u_7x?");
    drain(&h, &hashed);
    let e = "membrane::Chat:main:message:q:body";
    let fails = |b: Option<u64>| -> Vec<String> {
        cliffvm::expr::set_eval_budget(b);
        h.write(e, "recall_on", json!(false), "person"); h.pump();
        h.write(e, "recall_on", json!(true), "person"); h.pump();
        let f = h.get(e, "failed");
        cliffvm::expr::set_eval_budget(None);
        f.as_object().map(|m| m.keys().cloned().collect()).unwrap_or_default()
    };
    // (`r_older` fails here: this seat has no store, so the window is the ask alone)
    let at_default: Vec<String> = fails(None).into_iter().filter(|k| part(k) != "other").collect();
    eprintln!("VOTE_MEASURE texts {n}: under the default budget these fail: {at_default:?}");
    let full = |k: &str| h.get(e, k).as_array().map(|a| a.len()).unwrap_or(0);
    let sizes = format!("edge {} cosine {} move log {} entity {} recall {}", full("r_edge_rows"), full("r_cos_rows"), full("movelog_votes"), full("entity_votes"), full("recall"));
    // the steps of each getter in one recall (the engine's own accounting: calls and steps)
    cliffvm::stats::set_on(true);
    cliffvm::stats::global_clear();
    fails(None);
    let mut rows: Vec<(String, u64, u64)> = cliffvm::stats::global_lifetime().into_iter().filter(|r| r.cls == "SemText" && r.kind == "getter" && part(&r.prop) != "other").map(|r| (r.prop, r.calls, r.steps)).collect();
    rows.sort_by(|a, b| b.2.cmp(&a.2));
    cliffvm::stats::set_on(false);
    let bisect = |pick: &dyn Fn(&str) -> bool| -> u64 {
        let (mut lo, mut hi) = (1u64, 2_000_000u64);
        while hi - lo > 50 { let mid = (lo + hi) / 2; if fails(Some(mid)).iter().any(|k| pick(k)) { lo = mid } else { hi = mid } }
        hi
    };
    let whole = bisect(&|k: &str| part(k) != "other");
    eprintln!("VOTE_MEASURE texts {n} ({sizes}): the whole recall's largest evaluation takes about {whole} steps");
    for p in ["edge lane", "cosine lane", "move log", "entity recall", "vote, rerank, merge"] {
        eprintln!("VOTE_MEASURE texts {n}: {p}: about {} steps", bisect(&|k: &str| part(k) == p));
    }
    for (p, c, s) in rows.iter().take(14) { eprintln!("VOTE_MEASURE texts {n}: getter {p}: {c} calls, {s} steps"); }
}
