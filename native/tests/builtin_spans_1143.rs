//! `spans(text, layer)`: the tokenizer's builtin (#1143): `[{start, end, text}]`, offsets in chars.
use membrane_vm::fields::Fields;
use membrane_vm::vm::Core;
use serde_json::{json, Value as J};

fn ev(src: &str, env: J) -> J {
    serde_json::from_str(&membrane_vm::evaluate_json(src, &env.to_string()).unwrap()).unwrap()
}

#[test]
fn spans_returns_start_end_text_in_chars() {
    let e = json!({"t": "Привет, мир. Ok."});
    assert_eq!(ev("spans(t, 'sentence')", e.clone()), json!([{"start": 0, "end": 12, "text": "Привет, мир."}, {"start": 13, "end": 16, "text": "Ok."}]));
    assert_eq!(ev("[prop(s, 'text') for s in spans(t, 'word')]", e.clone()), json!(["Привет", "мир", "Ok"]));
    assert_eq!(ev("len(spans('', 'word'))", e.clone()), json!(0));
    // cutting by the offsets with the existing head/tail: no slice builtin is needed
    assert_eq!(ev("head(tail(t, len(t) - 8), 3)", e), json!("мир"));
}

#[test]
fn an_unknown_layer_is_an_error_naming_the_layers() {
    let e = json!({"t": "x"});
    let r = membrane_vm::evaluate_json("spans(t, 'paragraph')", &e.to_string());
    let m = format!("{:?}", r);
    assert!(m.contains("sentence") && m.contains("identifier"), "{m}");
}

fn read(pack: &str) -> J {
    let mut f = Fields::goalgraph();
    f.set_pack("t", pack).unwrap();
    let mut c = Core::new();
    c.load("Msg:a".into(), "Msg".into(), json!({"text": "Open ~/a.txt now. Then my_fn runs."}).as_object().unwrap().clone(), None, 0.0);
    let mut seq = 0;
    f.rebuild(&mut c, 1.0, &mut seq);
    c.docs["Msg:a"].props["s"].clone()
}

#[test]
fn a_getter_in_a_loaded_pack_reads_the_spans() {
    let s = read("PACK t 1\nMsg.s getter: spans(this.text, 'sentence')\n");
    assert_eq!(s, json!([{"start": 0, "end": 17, "text": "Open ~/a.txt now."}, {"start": 18, "end": 34, "text": "Then my_fn runs."}]));
    let s = read("PACK t 1\nMsg.s getter: [prop(x, 'text') for x in spans(this.text, 'path')]\n");
    assert_eq!(s, json!(["~/a.txt"]));
}

#[test]
fn the_inferencer_types_spans_and_checks_its_arguments() {
    use membrane_vm::infer::infer_src;
    assert_eq!(infer_src("spans(t, 'word')", &[("t", "Text")]).unwrap().0, "List(Dict)");
    assert_eq!(infer_src("len(spans(t, 'word'))", &[("t", "Text")]).unwrap().0, "Int");
    assert!(!infer_src("spans(t)", &[("t", "Text")]).unwrap().1.is_empty(), "one argument is reported");
}

#[cfg(feature = "bin")]
mod cliff {
    use membrane_vm::host::{Host, Options};
    use serde_json::json;
    /// the way the door's `run_cliff` loads a clifflet: the lines are the body of a getter on a scratch class
    #[test]
    fn a_clifflet_getter_reads_the_spans() {
        let mut o = Options::new(None); o.effects = false;
        let h = Host::open(o).unwrap();
        h.load_pack("cliffx", "PACK cliffx 1\nCliffx.val getter:\n  [prop(x, 'text') for x in spans('go to src/a.rs. Then stop.', 'sentence')]\n").unwrap();
        h.rebuild();
        h.born("Cliffx:x", "Cliffx", Default::default(), "person");
        h.pump();
        assert_eq!(h.get("Cliffx:x", "val"), json!(["go to src/a.rs.", "Then stop."]));
    }
}
