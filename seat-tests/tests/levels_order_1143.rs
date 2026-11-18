//! #1143: getters are ordered by what they read, class-aware. A getter reading `prop(x, 'text')` (x's class unknown) used to
//! be ordered after EVERY getter named `text`, whatever its class, which closed false cycles and left a getter computing from
//! a stale input. Also: levels stay cheap (they are computed at every pack load).
use membrane_vm::fields::{levels, levels_by_name, Fields, Getter};
use membrane_vm::vm::{Core, Host};
use serde_json::{json, Map, Value as J};

struct H;
impl Host for H {
    fn resolve(&self, _: &str) -> Option<String> { None }
    fn resolve_exact(&self, _: &str) -> Option<String> { None }
    fn dispatch(&self, _: &str, _: &str) -> Option<String> { None }
    fn vel(&self, _: &str, _: f64, _: f64) -> bool { false }
    fn align(&self, _: &str, _: f64) -> bool { false }
    fn live(&self, _: &str, _: bool) -> Option<String> { None }
}

/// Lamp.text (another class) reads Box.plan, and Box.src_text reads a field NAMED text off an entity of unknown class.
const COLLIDE: &str = "PACK collide 1\n\
Box.src_text getter: str(prop(this.src, 'text') or '')\n\
Box.src_name getter: str(this.src['text'] or '')\n\
Box.plan getter: this.src_text + '!'\n\
Box.plan2 getter: this.plan + this.src_name\n\
Lamp.text getter: join([b.plan2 for b in instances('Box')], ',')\n\
Lamp.shout getter: this.text + '?'\n";

fn gs(src: &str) -> Vec<Getter> { membrane_vm::syntax::parse_full(src).unwrap().getters }
fn lv_of(gs: &[Getter], cls: &str, prop: &str) -> usize { levels(gs)[gs.iter().position(|g| g.cls == cls && g.prop == prop).unwrap()] }

#[test]
fn a_name_read_off_an_unknown_class_does_not_order_a_getter_after_an_unrelated_class_getter() {
    let g = gs(COLLIDE);
    let (st, plan, p2, text) = (lv_of(&g, "Box", "src_text"), lv_of(&g, "Box", "plan"), lv_of(&g, "Box", "plan2"), lv_of(&g, "Lamp", "text"));
    assert!(st < plan && plan < p2 && p2 < text, "levels {st} {plan} {p2} {text}");
}

fn world(f: &mut Fields) -> Core {
    let mut core = Core::new();
    let mut p = Map::new();
    p.insert("src".into(), json!({"text": "hi"}));
    core.load("Box:b".into(), "Box".into(), p, None, 0.0);
    let mut seq = 0i64;
    f.settle(&mut core, &H, 1.0, &mut seq);
    core
}

#[test]
fn the_getter_computes_from_the_fresh_input_after_a_write() {
    let mut f = Fields::goalgraph();
    f.set_pack("collide", COLLIDE).unwrap();
    let mut core = world(&mut f);
    assert_eq!(core.docs["Box:b"].props.get("plan"), Some(&json!("hi!")));
    let old = core.docs["Box:b"].props.clone();
    let mut p = old.clone();
    p.insert("src".into(), json!({"text": "yo"}));
    core.load("Box:b".into(), "Box".into(), p, None, 2.0);
    let new = core.docs["Box:b"].props.clone();
    f.reloaded("Box:b", &old, &new);
    let mut seq = 0i64;
    f.settle(&mut core, &H, 3.0, &mut seq);
    assert_eq!(core.docs["Box:b"].props.get("plan"), Some(&json!("yo!")));
    assert_eq!(core.docs["Box:b"].props.get("plan2"), Some(&json!("yo!yo")));
}

// ---- the new levels agree with the old (by name) wherever the old name scan was not the bug ----

fn shipped_getters() -> Vec<Getter> {
    let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs");
    let mut files: Vec<_> = std::fs::read_dir(dir).unwrap().filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.extension().map_or(false, |x| x == "c1f")).collect();
    files.sort();
    let mut all = gs(membrane_vm::translate::GOALGRAPH_CH);
    for p in files { all.extend(gs(&std::fs::read_to_string(p).unwrap())); }
    all
}

#[test]
fn synthetic_packs_with_chains_cycles_and_shared_names_level_as_before() {
    for src in [
        "PACK s 1\nA.a getter: this.b + 1\nA.b getter: this.c + 1\nA.c getter: 1\n",
        "PACK s 1\nA.a getter: this.b\nA.b getter: this.c\nA.c getter: this.a\n",
        "PACK s 1\nA.a getter: this.b + this.a\nA.b getter: this.a\nA.d getter: {'a': 1, 'b': ['c']}\n",
        "PACK s 1\nA.x getter: this.y\nA.y getter: 1\nB.x getter: this.z\nB.z getter: this.y\nB.y getter: 2\n",
        "PACK s 1\nA.l getter: [g.n for g in instances('G')]\nG.n getter: this.m\nG.m getter: 3\n",
    ] {
        let g = gs(src);
        assert_eq!(levels(&g), levels_by_name(&g), "{src}");
    }
}

#[test]
fn shipped_packs_level_as_before_except_where_the_name_scan_was_the_bug() {
    let g = shipped_getters();
    let (new, old) = (levels(&g), levels_by_name(&g));
    let diff: Vec<String> = (0..g.len()).filter(|&i| new[i] != old[i]).map(|i| format!("{}.{}: {} -> {}", g[i].cls, g[i].prop, old[i], new[i])).collect();
    eprintln!("{} getters, {} levels differ:\n{}", g.len(), diff.len(), diff.join("\n"));
    // the order that matters: a getter above every getter it provably reads (a known class). A cycle may keep a violation (its
    // closing edge reads the previous value); the new levels must violate no more edges than the old ones did.
    let triples: Vec<(&str, &str, &str)> = g.iter().map(|x| (x.cls.as_str(), x.prop.as_str(), x.expr.as_str())).collect();
    let reads = membrane_vm::infer::getter_reads(&triples);
    let (mut vn, mut vo, mut edges) = (0, 0, 0);
    for (i, rs) in reads.iter().enumerate() {
        for r in rs.iter().flatten() {
            let Some(c) = &r.cls else { continue };
            for (j, y) in g.iter().enumerate() {
                if &y.cls == c && y.prop == r.name && !(y.cls == g[i].cls && y.prop == g[i].prop) {
                    edges += 1;
                    if new[i] <= new[j] { vn += 1; }
                    if old[i] <= old[j] { vo += 1; }
                }
            }
        }
    }
    eprintln!("known edges {edges}: violated by the new levels {vn}, by the old {vo}");
    assert!(vn <= vo, "the new levels break {vn} known reads, the old {vo}");
    assert_eq!(vn, 0, "a known read is ordered after its reader");
}

// ---- cost ----

#[test]
fn levels_stay_cheap_on_the_shipped_packs_and_on_a_2000_getter_pack() {
    let g = shipped_getters();
    let t = std::time::Instant::now();
    levels(&g);
    let shipped = t.elapsed();
    let mut src = String::from("PACK big 1\n");
    for i in 0..2000 { src.push_str(&format!("C{}.v{} getter: this.v{} + len(this.name) + [t.text for t in instances('X')]\n", i % 40, i, if i == 0 { 0 } else { i - 1 })); }
    let big = gs(&src);
    let t = std::time::Instant::now();
    levels(&big);
    let synth = t.elapsed();
    eprintln!("levels: shipped {} getters {:?}; synthetic {} getters {:?}", g.len(), shipped, big.len(), synth);
    assert!(shipped.as_secs_f64() < 1.0, "shipped {shipped:?}");
    assert!(synth.as_secs_f64() < 2.0, "synthetic {synth:?}");
    let _ = J::Null;
}

#[test]
fn the_intents_pack_written_with_prop_and_attribute_reads_still_orders_its_plan_last() {
    // the form the pack had before its subscript workaround (cc73e00): `prop(this.intend, 'text')` and `g.text`
    let src = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../membrane/packs/intents.c1f")).unwrap()
        .replace("this.intend['text']", "prop(this.intend, 'text')").replace("this.intend['by']", "prop(this.intend, 'by')")
        .replace("g['text'] or g['name']", "g.text or g.name")
        .replace("this.fulfill['spec']", "prop(this.fulfill, 'spec')");
    assert!(src.contains("prop(this.intend, 'text')") && src.contains("g.text or g.name"));
    let mut all: Vec<Getter> = shipped_getters().into_iter().filter(|x| x.cls != "Intent").collect();
    all.extend(gs(&src));
    let lv = |p: &str| lv_of(&all, "Intent", p);
    assert!(lv("in_text") < lv("in_plain") && lv("in_plain") < lv("in_match") && lv("in_match") < lv("intend_plan"));
    assert!(lv("ful_spec") < lv("ful_named") && lv("ful_named") < lv("fulfill_plan"));
}
