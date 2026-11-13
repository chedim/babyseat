//! LAYERS (docs/c1f-layers.md): a later pack is above the packs it imports. Getters: the arms of every layer form ONE list, the top layer's
//! first, the first WHEN that holds wins and falls through, an upper default ends it. Setters: all fire top first; a same-name setter in an
//! upper layer replaces the lower one at compose; stacked setters writing one target: the FIRST (the top's) write wins, the later line is
//! skipped before its right-hand side is evaluated and without running any effect, quietly across layers. `Why` names the layer.
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

const BASE: &str = "PACK base 1\nLight.word getter WHEN this.level > 0: 'on'\nLight.word getter: 'off'\nLight.glow getter: this.level * 2\n";

fn world(packs: &[(&str, &str)]) -> (Core, Fields) {
    let mut f = Fields::goalgraph();
    for (n, t) in packs { f.set_pack(n, t).unwrap_or_else(|e| panic!("{n}: {e}")); }
    let mut c = Core::new();
    c.load("Light:a".into(), "Light".into(), json!({"level": 0}).as_object().unwrap().clone(), None, 0.0);
    c.load("Box:b".into(), "Box".into(), Default::default(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    (c, f)
}
fn put(c: &mut Core, f: &mut Fields, eid: &str, p: &str, v: J) {
    let w = Write { eid: eid.into(), prop: p.into(), new: v, old: J::Null, writer: "person".into(), chain: json!({}), seq: 9 };
    c.hear(w.clone(), None, 2.0);
    let mut seq = 10;
    f.react(c, &H, w, 2.0, &mut seq);
}
fn word(c: &Core) -> J { c.docs["Light:a"].props.get("word").cloned().unwrap_or(J::Null) }

// ---- getters -----------------------------------------------------------------------------------------------------------------

#[test]
fn an_upper_layers_arms_come_first_and_the_search_falls_through_to_the_lower() {
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'bright'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("upper", upper)]);
    put(&mut c, &mut f, "Light:a", "level", json!(9));
    assert_eq!(word(&c), json!("bright"));
    put(&mut c, &mut f, "Light:a", "level", json!(2));
    assert_eq!(word(&c), json!("on"), "no upper arm holds: the lower's arms answer");
    put(&mut c, &mut f, "Light:a", "level", json!(-1));
    assert_eq!(word(&c), json!("off"));
}

#[test]
fn an_upper_default_arm_ends_the_search() {
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'bright'\nLight.word getter: 'always'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("upper", upper)]);
    put(&mut c, &mut f, "Light:a", "level", json!(9));
    assert_eq!(word(&c), json!("bright"));
    put(&mut c, &mut f, "Light:a", "level", json!(2));
    assert_eq!(word(&c), json!("always"), "the upper ELSE ends it: the lower `on` never answers");
}

#[test]
fn removing_the_upper_layer_gives_the_lower_arms_back() {
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter: 'mine'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("upper", upper)]);
    put(&mut c, &mut f, "Light:a", "level", json!(3));
    assert_eq!(word(&c), json!("mine"));
    let mut seq = 50;
    f.remove_pack_clean(&mut c, &H, "upper", 3.0, &mut seq);
    f.settle(&mut c, &H, 3.0, &mut seq);
    put(&mut c, &mut f, "Light:a", "level", json!(4));
    assert_eq!(word(&c), json!("on"));
}

#[test]
fn an_upper_arm_reading_another_getter_is_ordered_after_it() {
    // glow is a getter of the base; the upper arm reads it: the round computes glow first (levels), then the stacked word
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.glow > 10: 'dazzling'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("upper", upper)]);
    put(&mut c, &mut f, "Light:a", "level", json!(6));
    assert_eq!(c.docs["Light:a"].props["glow"], json!(12));
    assert_eq!(word(&c), json!("dazzling"));
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    assert_eq!(word(&c), json!("on"));
}

#[test]
fn a_layer_overrides_only_what_it_imports() {
    // `rogue` imports base (it may declare on Light) but not `upper`, which also defines Light.word: refused at load, with the way out
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter: 'mine'\n";
    let rogue = "PACK rogue 1\nIMPORT base 1\nLight.word getter WHEN this.level > 100: 'huge'\n";
    let mut f = Fields::goalgraph();
    f.set_pack("base", BASE).unwrap();
    f.set_pack("upper", upper).unwrap();
    let e = f.set_pack("rogue", rogue).unwrap_err();
    assert!(e.contains("Light.word") && e.contains("upper") && e.contains("IMPORT upper"), "{e}");
    assert!(!f.pack_active("rogue"));
    // with the import it loads, and it is the top layer
    let rogue2 = "PACK rogue 1\nIMPORT base 1\nIMPORT upper 1\nLight.word getter WHEN this.level > 100: 'huge'\n";
    f.set_pack("rogue", rogue2).unwrap();
    assert!(f.pack_active("rogue"));
}

#[test]
fn why_names_the_layer_that_supplied_a_value() {
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'bright'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("upper", upper)]);
    put(&mut c, &mut f, "Light:a", "level", json!(9));
    assert_eq!(f.why_layer(&c, "Light:a", "word"), vec!["layer: upper".to_string()]);
    put(&mut c, &mut f, "Light:a", "level", json!(2));
    assert_eq!(f.why_layer(&c, "Light:a", "word"), vec!["layer: base".to_string()], "the arm that held is the lower's");
    assert!(f.why_layer(&c, "Light:a", "glow").is_empty() || f.why_layer(&c, "Light:a", "glow") == vec!["layer: base".to_string()]);
}

#[test]
fn the_inferred_types_see_the_arms_of_every_pack() {
    let upper = "PACK upper 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'bright'\n";
    let r = membrane_vm::infer::infer_packs(&[BASE, upper]).expect("both parse");
    assert!(r[0].1.getters.contains_key("Light.word") && r[1].1.getters.contains_key("Light.word"));
}

// ---- setters -----------------------------------------------------------------------------------------------------------------

const LOW: &str = "PACK low 1\nIMPORT base 1\nLight.level on-setter mark:\n  ALWAYS:\n    Box:b.m = 'lower'\nLight.level on-setter lowv:\n  ALWAYS:\n    Box:b.v = 'lower'\n";

fn writers(f: &Fields) -> Vec<String> { f.out.iter().filter_map(|w| w["writer"].as_str().map(String::from)).filter(|w| w.starts_with("field:") && !w.contains("getter")).collect() }

#[test]
fn every_setter_fires_top_layer_first() {
    let up = "PACK up 1\nIMPORT base 1\nIMPORT low 1\nLight.level on-setter upv:\n  ALWAYS:\n    Box:b.w = 'upper'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("low", LOW), ("up", up)]);
    f.out.clear();
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    let w = writers(&f);
    let at = |n: &str| w.iter().position(|x| x == &format!("field:{n}")).unwrap_or_else(|| panic!("{n} in {w:?}"));
    assert!(at("upv") < at("mark") && at("upv") < at("lowv"), "the top layer's setter fires first: {w:?}");
}

#[test]
fn a_same_name_setter_in_an_upper_layer_replaces_the_lower_one() {
    let up = "PACK up 1\nIMPORT base 1\nIMPORT low 1\nLight.level on-setter mark:\n  ALWAYS:\n    Box:b.u = 'upper'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("low", LOW), ("up", up)]);
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    assert_eq!(c.docs["Box:b"].props["u"], json!("upper"));
    assert!(c.docs["Box:b"].props.get("m").is_none(), "the lower `mark` is dropped at compose and never evaluated");
    assert_eq!(c.docs["Box:b"].props["v"], json!("lower"), "the lower's other setters stay");
}

#[test]
fn a_setter_replaced_by_an_unrelated_layer_is_refused_at_load() {
    let rogue = "PACK rogue 1\nIMPORT base 1\nLight.level on-setter mark:\n  ALWAYS:\n    Box:b.u = 'rogue'\n";
    let mut f = Fields::goalgraph();
    f.set_pack("base", BASE).unwrap();
    f.set_pack("low", LOW).unwrap();
    let e = f.set_pack("rogue", rogue).unwrap_err();
    assert!(e.contains("mark") && e.contains("low") && e.contains("IMPORT low"), "{e}");
}

#[test]
fn stacked_setters_writing_one_target_the_first_write_wins_quietly_and_why_says_who() {
    let up = "PACK up 1\nIMPORT base 1\nIMPORT low 1\nLight.level on-setter upv:\n  ALWAYS:\n    Box:b.v = 'upper'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("low", LOW), ("up", up)]);
    f.refusals.clear();
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    assert_eq!(c.docs["Box:b"].props["v"], json!("upper"), "the top layer's write stands");
    assert!(f.refusals.is_empty(), "across layers the override is quiet, not the loop error: {:?}", f.refusals);
    assert!(c.docs["Box:b"].props.get("refused").is_none());
    let why = f.why_layer(&c, "Box:b", "v");
    assert!(why.iter().any(|l| l == "overridden by up: low's lowv write skipped"), "{why:?}");
}

#[test]
fn inside_one_layer_a_double_write_stays_the_error() {
    let p = "PACK dbl 1\nIMPORT base 1\nLight.level on-setter a:\n  ALWAYS:\n    Box:b.v = 1\n    Box:b.v = 2\n";
    let (mut c, mut f) = world(&[("base", BASE), ("dbl", p)]);
    f.refusals.clear();
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    assert_eq!(c.docs["Box:b"].props["v"], json!(1));
    assert!(f.refusals.iter().any(|r| r["reason"].as_str().unwrap_or("").contains("already written this round")), "{:?}", f.refusals);
}

#[test]
fn a_shadowed_write_runs_no_effect() {
    let d = std::env::temp_dir().join(format!("layers-fx-{}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    let low = "PACK low 1\nIMPORT base 1\nLight.level on-setter lowrun:\n  ALWAYS:\n    Command:c.run = {'id': 'lower'}\n";
    let up = "PACK up 1\nIMPORT base 1\nIMPORT low 1\nLight.level on-setter uprun:\n  ALWAYS:\n    Command:c.run = {'id': 'upper'}\n";
    let mut f = Fields::goalgraph();
    for (n, t) in [("base", BASE), ("low", low), ("up", up)] { f.set_pack(n, t).unwrap(); }
    let mut c = Core::new();
    c.store = Some(Store::io_only(d.to_str().unwrap()));
    c.load("Light:a".into(), "Light".into(), json!({"level": 0}).as_object().unwrap().clone(), None, 0.0);
    c.load("Box:b".into(), "Box".into(), Default::default(), None, 0.0);
    c.load("Command:c".into(), "Command".into(), json!({"cmd": ["sh", "-c", "echo hi"]}).as_object().unwrap().clone(), None, 0.0);
    let mut s = 0; f.rebuild(&mut c, 1.0, &mut s);
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    let w = c.store.as_ref().unwrap().waiter();
    let t = std::time::Instant::now();
    while w.pending() > 0 && t.elapsed().as_secs() < 10 { w.wait(Some(std::time::Duration::from_millis(100))); let mut s = 30; f.settle(&mut c, &H, 3.0, &mut s); }
    let mut s = 40; f.settle(&mut c, &H, 3.0, &mut s);
    assert_eq!(c.docs["Command:c"].props["run_result"]["id"], json!("upper"), "only the top layer's command ran");
    let _ = std::fs::remove_dir_all(d);
}

// ---- the stale-setter check sees every layer ----------------------------------------------------------------------------------

#[test]
fn a_setter_in_one_layer_reading_a_getter_of_another_that_depends_on_its_trigger_is_refused() {
    // the lower getter reads level; the upper setter (trigger level) reads that getter: stale, as inside one pack
    let up = "PACK up 1\nIMPORT base 1\nLight.level on-setter s:\n  ALWAYS:\n    Box:b.w = this.word\n";
    let mut f = Fields::goalgraph();
    f.set_pack("base", BASE).unwrap();
    let e = f.set_pack("up", up).unwrap_err();
    assert!(e.contains("reads the getter Light.word") && e.contains("on-setter s"), "{e}");
    assert!(!f.pack_active("up"));
}

#[test]
fn an_upper_arm_that_makes_a_getter_depend_on_a_lower_setters_trigger_is_refused() {
    // the lower setter reads word, which does not depend on level; the upper adds an arm reading level: the lower setter would read it stale
    let base = "PACK base 1\nLight.kind getter: 'light'\nLight.word getter: 'plain'\nLight.level on-setter s:\n  ALWAYS:\n    Box:b.w = this.word\n";
    let up = "PACK up 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'bright'\n";
    let mut f = Fields::goalgraph();
    f.set_pack("base", base).unwrap();
    let e = f.set_pack("up", up).unwrap_err();
    assert!(e.contains("reads the getter Light.word"), "{e}");
    assert!(!f.pack_active("up"));
}

// ---- the scoped override exception: an import of B lets a pack override what B defines, without importing the class owner ----------
// RULE A as built: a declaration on a class owned by another pack needs `IMPORT <owner>` UNLESS EVERY declaration the pack makes on that class
// overrides a definition (getter: class+property; setter: class+property+name) of a pack it imports (directly or through its imports).

const MID: &str = "PACK mid 1\nIMPORT base 1\nLight.word getter WHEN this.level > 5: 'mid'\n";

#[test]
fn importing_only_the_overridden_pack_overrides_its_getter() {
    let up = "PACK up 1\nIMPORT mid 1\nLight.word getter WHEN this.level > 7: 'up'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("mid", MID), ("up", up)]);
    put(&mut c, &mut f, "Light:a", "level", json!(9));
    assert_eq!(word(&c), json!("up"), "the upper arm answers");
    put(&mut c, &mut f, "Light:a", "level", json!(6));
    assert_eq!(word(&c), json!("mid"));
}

#[test]
fn importing_only_the_overridden_pack_overrides_its_same_named_setter() {
    let up = "PACK up 1\nIMPORT low 1\nLight.level on-setter mark:\n  ALWAYS:\n    Box:b.u = 'upper'\n";
    let (mut c, mut f) = world(&[("base", BASE), ("low", LOW), ("up", up)]);
    put(&mut c, &mut f, "Light:a", "level", json!(1));
    assert_eq!(c.docs["Box:b"].props["u"], json!("upper"));
    assert!(c.docs["Box:b"].props.get("m").is_none(), "the lower `mark` is replaced");
}

fn refused(packs: &[(&str, &str)], last: (&str, &str)) -> String {
    let mut f = Fields::goalgraph();
    for (n, t) in packs { f.set_pack(n, t).unwrap(); }
    f.set_pack(last.0, last.1).unwrap_err()
}

#[test]
fn a_new_getter_on_the_class_still_needs_the_owner_import() {
    let up = "PACK up 1\nIMPORT mid 1\nLight.glow getter: 1\n";
    let e = refused(&[("base", BASE), ("mid", MID)], ("up", up));
    assert!(e.contains("belongs to pack base") && e.contains("add IMPORT base"), "{e}");
}

#[test]
fn a_new_setter_on_the_class_still_needs_the_owner_import() {
    let up = "PACK up 1\nIMPORT low 1\nLight.level on-setter fresh:\n  ALWAYS:\n    Box:b.u = 'x'\n";
    let e = refused(&[("base", BASE), ("low", LOW)], ("up", up));
    assert!(e.contains("belongs to pack base") && e.contains("add IMPORT base"), "{e}");
}

#[test]
fn overriding_what_an_unimported_pack_defines_is_still_refused() {
    let other = "PACK other 1\nIMPORT base 1\nLight.shine getter: 'o'\n";
    // owner imported, `other` not: the override rule names `other`
    let e = refused(&[("base", BASE), ("other", other)], ("rogue", "PACK rogue 1\nIMPORT base 1\nLight.shine getter: 'r'\n"));
    assert!(e.contains("Light.shine") && e.contains("IMPORT other"), "{e}");
    // neither imported but `mid` is: still refused
    let e = refused(&[("base", BASE), ("mid", MID), ("other", other)], ("rogue", "PACK rogue 1\nIMPORT mid 1\nLight.shine getter: 'r'\n"));
    assert!(e.contains("add IMPORT"), "{e}");
}

#[test]
fn a_pack_that_overrides_and_adds_on_one_class_needs_the_owner_import() {
    let up = "PACK up 1\nIMPORT mid 1\nLight.word getter WHEN this.level > 7: 'up'\nLight.glow getter: 3\n";
    let e = refused(&[("base", BASE), ("mid", MID)], ("up", up));
    assert!(e.contains("belongs to pack base") && e.contains("add IMPORT base"), "{e}");
    // with the owner import the same pack loads
    let ok = "PACK up 1\nIMPORT base 1\nIMPORT mid 1\nLight.word getter WHEN this.level > 7: 'up'\nLight.glow getter: 3\n";
    let mut f = Fields::goalgraph();
    for (n, t) in [("base", BASE), ("mid", MID), ("up", ok)] { f.set_pack(n, t).unwrap(); }
}

#[test]
fn a_first_declaration_that_overrides_nothing_needs_the_import() {
    let e = refused(&[("base", BASE), ("mid", MID)], ("up", "PACK up 1\nLight.word getter: 'x'\n"));
    assert!(e.contains("belongs to pack base") && e.contains("add IMPORT base"), "no import at all: {e}");
    let e = refused(&[("base", BASE), ("mid", MID)], ("up", "PACK up 1\nIMPORT mid 1\nLight.level on-setter first:\n  ALWAYS:\n    Box:b.u = 'x'\n"));
    assert!(e.contains("belongs to pack base"), "{e}");
}

#[test]
fn the_exception_is_not_transitive_what_the_owner_defines_needs_the_owner_import() {
    // `up` imports mid, which imports base: base's own Light.glow is not mid's to hand over
    let e = refused(&[("base", BASE), ("mid", MID)], ("up", "PACK up 1\nIMPORT mid 1\nLight.glow getter: 3\n"));
    assert!(e.contains("belongs to pack base") && e.contains("add IMPORT base"), "{e}");
}
