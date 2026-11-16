//! Null reasons and the mention cascade (#1143, the model's live findings): a missing property is a null that says `absent`
//! (naming the entity and the property); a reasoned null inside a list or a dict keeps its reason.
use membrane_vm::host::{Host, Options};
use serde_json::{json, Value as J};

fn host(pack: &str) -> Host {
    let mut o = Options::new(None);
    o.effects = false;
    let h = Host::open(o).unwrap();
    h.load_pack("sem", pack).unwrap_or_else(|e| panic!("pack refused: {e}"));
    h.rebuild();
    h
}
fn born(h: &Host, eid: &str, props: J) {
    h.born(eid, eid.split(':').next().unwrap(), props.as_object().unwrap().clone(), "person");
    h.pump();
}

#[test]
fn a_missing_property_is_a_null_that_says_absent_and_names_the_entity_and_the_property() {
    let h = host("PACK sem 1\nBox.why getter: why(this.price)\nBox.viaprop getter: why(prop(this, 'cost'))\n");
    born(&h, "Box:a", json!({}));
    let w = h.get("Box:a", "why");
    let s = w.as_str().unwrap_or_else(|| panic!("why(missing) is {w}, the help says the reason `absent`"));
    assert!(s.starts_with("absent") && s.contains("Box:a") && s.contains("price"), "{s}");
    let p = h.get("Box:a", "viaprop");
    assert!(p.as_str().is_some_and(|s| s.starts_with("absent") && s.contains("cost")), "{p}");
}

#[test]
fn a_missing_property_is_still_null_the_old_ways() {
    let h = host("PACK sem 1\nBox.a getter: this.price or 7\nBox.b getter: this.price == None\nBox.c getter: 'x' if this.price else 'y'\n");
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "a"), json!(7));
    assert_eq!(h.get("Box:a", "b"), json!(true));
    assert_eq!(h.get("Box:a", "c"), json!("y"));
}

// ---- item 2: a reasoned null inside a list or a dict keeps its reason ----

#[test]
fn a_reasoned_null_in_a_list_or_a_dict_keeps_its_reason_in_an_expression_and_in_a_stored_value() {
    let h = host("PACK sem 1\n\
Box.l getter: [this.price, 3]\n\
Box.d getter: {'a': this.price, 'b': {'c': None('boom')}}\n\
Box.in_expr getter: why([this.price][0])\n\
Box.lw getter: why(this.l[0])\n\
Box.lw1 getter: why(this.l[1])\n\
Box.dw getter: why(this.d['a'])\n\
Box.dw2 getter: why(this.d['b']['c'])\n");
    born(&h, "Box:a", json!({}));
    let s = |p: &str| h.get("Box:a", p);
    assert!(s("in_expr").as_str().is_some_and(|x| x.starts_with("absent") && x.contains("price")), "in an expression: {}", s("in_expr"));
    assert!(s("lw").as_str().is_some_and(|x| x.starts_with("absent") && x.contains("price")), "in a stored list: {}", s("lw"));
    assert_eq!(s("lw1"), J::Null, "a number has no reason");
    assert!(s("dw").as_str().is_some_and(|x| x.contains("price")), "in a stored dict: {}", s("dw"));
    assert_eq!(s("dw2"), json!("boom"), "nested in a dict in a dict");
    assert_eq!(s("l"), json!([null, 3]), "what is stored and sent is unchanged: a plain null");
    // the value moves on: the reason goes
    h.write("Box:a", "price", json!(5), "person");
    h.pump();
    assert_eq!(s("l"), json!([5, 3]));
    assert_eq!(s("lw"), J::Null, "a number has no reason any more");
}

#[test]
fn str_of_a_container_shows_the_reason_of_a_null_inside_it() {
    let h = host("PACK sem 1\nBox.l getter: [this.price, 3]\nBox.t getter: str(this.l)\nBox.t2 getter: str({'k': None('boom')})\nBox.t3 getter: str([None])\n");
    born(&h, "Box:a", json!({}));
    let t = h.get("Box:a", "t");
    assert!(t.as_str().is_some_and(|x| x.contains("absent") && x.contains("price")), "{t}");
    assert!(h.get("Box:a", "t2").as_str().is_some_and(|x| x.contains("boom")));
    assert_eq!(h.get("Box:a", "t3"), json!("[null]"), "a null with no reason stays null");
}

// ---- item 4: a class a pack counts with `instances` is declared RESIDENT by the pack that owns it; a trigger takes a new value each time ----
const WATCH: &str = "Keep.line getter: 'k ' + str(this.title)\n\
Watch.keeps_now getter: len([k for k in instances('Keep') if k.done != True])\n\
Watch.snap on-setter snapshot:\n  ALWAYS:\n    this.last_keeps = this.keeps_now\n\
Watch.alert getter: 'ALERT' if this.last_keeps != None and this.keeps_now < this.last_keeps else ''\n";

fn store_file(tag: &str) -> String {
    std::env::temp_dir().join(format!("sem-{tag}-{}-{}.sqlite3", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos())).to_string_lossy().to_string()
}
fn open(file: &str) -> Host { let mut o = Options::new(Some(file.to_string())); o.effects = false; Host::open(o).unwrap() }

#[test]
fn a_class_the_owner_declares_resident_is_counted_whole_after_a_restart_where_a_lazy_one_is_not() {
    let file = store_file("resident");
    {
        let h = open(&file);
        h.load_pack("plan", &format!("PACK plan 1\n{WATCH}")).unwrap();
        h.rebuild();
        for i in 0..6 { h.born(&format!("Keep:k{i}"), "Keep", json!({"title": format!("t{i}")}).as_object().unwrap().clone(), "person"); }
        h.born("Watch:main", "Watch", Default::default(), "person");
        h.pump();
    }
    let count = |header: &str| {
        let h = open(&file);
        h.set_warm_rows(0);
        h.load_pack("plan", &format!("PACK plan 1\n{header}{WATCH}")).unwrap();
        h.load_stored_entities();
        h.rebuild();
        h.pump();
        h.get("Watch:main", "keeps_now")
    };
    let lazy = count("");
    assert!(lazy.as_i64().unwrap_or(0) < 6, "a lazy class lists only what is loaded: {lazy}");
    assert_eq!(count("RESIDENT Keep\n"), json!(6), "the owner declares it RESIDENT: every stored Keep is counted");
    let _ = std::fs::remove_file(&file);
}

#[test]
fn a_write_of_the_value_a_property_holds_runs_no_setter_and_a_new_value_does() {
    // the door's `entity_write` is a setter that writes `<entity>.<prop> = <value>` (door_tools.c1f `mcp-write`): a write by the field, which changes nothing when the value is the held one
    let h = host(&format!("PACK sem 1\n{WATCH}Door.go on-setter w:\n  ALWAYS:\n    Watch:main.snap = this.go\n"));
    born(&h, "Watch:main", json!({}));
    born(&h, "Door:d", json!({}));
    let keep = |i: usize| { h.born(&format!("Keep:k{i}"), "Keep", json!({"title": "t"}).as_object().unwrap().clone(), "person"); h.pump(); };
    let go = |v: i64| { h.write("Door:d", "go", json!(v), "person"); h.pump(); };
    (0..3).for_each(keep);
    go(1);
    assert_eq!(h.get("Watch:main", "last_keeps"), json!(3), "the first snap");
    keep(3);
    go(1);          // the door is written again and writes 1 again: the held value, nothing runs
    assert_eq!(h.get("Watch:main", "last_keeps"), json!(3), "writing 1 over 1 runs nothing: the alert would stay");
    go(2);          // a new value runs it
    assert_eq!(h.get("Watch:main", "last_keeps"), json!(4));
}

// ---- a missing ENTITY says so too (#1143, a live finding): `why(Box:zzz.total)` was a plain null ----

#[test]
fn a_missing_entity_is_a_null_that_says_it_does_not_exist_and_a_missing_property_on_a_real_one_still_names_the_property() {
    let h = host("PACK sem 1\nBox.ent getter: why(prop('Box:zzz', 'total'))\nBox.lit getter: why(Box:zzz.total)\nBox.real getter: why(prop('Box:b', 'total'))\nBox.alt getter: prop('Box:zzz', 'total') or 5\nBox.isnone getter: prop('Box:zzz', 'total') == None\n");
    born(&h, "Box:b", json!({}));
    born(&h, "Box:a", json!({}));
    assert_eq!(h.get("Box:a", "ent"), json!("absent: Box:zzz does not exist"));
    assert_eq!(h.get("Box:a", "lit"), json!("absent: Box:zzz does not exist"));
    assert_eq!(h.get("Box:a", "real"), json!("absent: Box:b.total"));
    assert_eq!(h.get("Box:a", "alt"), json!(5), "still null the old ways");
    assert_eq!(h.get("Box:a", "isnone"), json!(true));
}
