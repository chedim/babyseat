//! #994 syntax pass: cliffhanger's text form for field setters (src/syntax.rs). The goal graph ships as text
//! (packs/goalgraph.c1f); the old WHEN/WHERE/WRITE triggers migrate into it through the translator, unchanged.
use membrane_vm::fields::{OnSet, LLM_CALL};
use membrane_vm::syntax::{parse, print};
use membrane_vm::translate::{goalgraph_setters, translated_goalgraph_setters, GOALGRAPH_CH};

#[test]
fn the_text_pack_equals_the_migrated_triggers() {
    assert_eq!(goalgraph_setters(), translated_goalgraph_setters());
    assert_eq!(goalgraph_setters().len(), 9);
}

#[test]
fn print_then_parse_is_the_same_setters() {
    let s = goalgraph_setters();
    assert_eq!(parse(&print(&s)).unwrap(), s);
    assert!(GOALGRAPH_CH.contains("this.goal.replan = 'task ' + this.eid"));
}

#[test]
fn branches_inline_statements_and_always() {
    let src = "
-- a comment line
Task.status on-setter tidy:   -- a comment after the header
  WHEN this.status == 'done': this.note = 'done -- not a comment'
  ELSE WHEN this.status == 'stuck':
    this.note = 'stuck'
    this.goal.replan = 'stuck: ' + this.eid
  ELSE:
    this.note = ''
Goal.replan on-setter plan:
  ALWAYS: membrane::llm.call = {'about': this.eid, 'lane': 'planner'}
Goal.status on-setter bare:
  this.seen = True
";
    let got = parse(src).unwrap();
    let w = |t: &str, p: &str, v: &str| (t.to_string(), p.to_string(), v.to_string());
    assert_eq!(got[0], OnSet { name: "tidy".into(), cls: "Task".into(), prop: "status".into(),
        when: "this.status == 'done'".into(), writes: vec![w("this", "note", "'done -- not a comment'")],
        els: vec![("this.status == 'stuck'".into(), vec![w("this", "note", "'stuck'"), w("this.goal", "replan", "'stuck: ' + this.eid")]),
                  ("True".into(), vec![w("this", "note", "''")])] });
    assert_eq!(got[1].when, "True");
    assert_eq!(got[1].writes, vec![w(LLM_CALL, "", "{'about': this.eid, 'lane': 'planner'}")]);
    assert_eq!(got[2].when, "True");
    assert_eq!(got[2].writes, vec![w("this", "seen", "True")]);
    assert_eq!(parse(&print(&got)).unwrap(), got);
}

#[test]
fn mistakes_are_named_with_their_line() {
    let err = |src: &str| parse(src).unwrap_err();
    assert!(err("Task.status on-setter a:\n  WHEN this.x == 1: this.y = 1\n  WHEN this.x == 2: this.y = 2\n").contains("line 3: a second WHEN"));
    assert!(err("Task.status on-setter a:\n  ELSE: this.y = 1\n").contains("line 2: ELSE must follow a WHEN"));
    assert!(err("Task.status on-setter a:\n  WHEN this.x == 1 this.y = 1\n").contains("needs a `:`"));
    assert!(err("Task.status on-setter a:\n  WHEN this.x == 1: status = 1\n").contains("write `this.status`"));
    assert!(err("Task.status on-setter a:\n  WHEN this.x == 1: this.y == 1\n").contains("expected `<target>.<field> = <expr>`"));
    assert!(err("Task.status on-setter:\n  this.y = 1\n").contains("expected `<Class>.<field> on-setter <name>:`"));
    assert!(err("Task.status on-setter a:\n").contains("no body"));
    assert!(err("  this.y = 1\n").contains("before any setter header"));
}

#[test]
fn packs_load_at_runtime_replace_by_name_and_a_broken_one_changes_nothing() {
    use membrane_vm::fields::Fields;
    let mut f = Fields::goalgraph();
    let base = (f.setters.len(), f.getters.len());
    assert!(!f.classes().contains(&"Lamp".to_string()));
    f.set_pack("lamps", "Lamp.power on-setter glow:\n  WHEN this.power == 'on': this.lit = True\n  ELSE: this.lit = False\nLamp.watts getter: (this.power == 'on') * 60\n").unwrap();
    assert_eq!((f.setters.len(), f.getters.len()), (base.0 + 1, base.1 + 1));
    assert!(f.classes().contains(&"Lamp".to_string()));
    f.set_pack("lamps", "Lamp.power on-setter glow:\n  ALWAYS: this.lit = True\n").unwrap();     // replaced, not added
    assert_eq!((f.setters.len(), f.getters.len()), (base.0 + 1, base.1));
    assert!(f.set_pack("lamps", "Lamp.power on-setter:\n  this.lit = 1\n").unwrap_err().contains("pack lamps"));
    assert_eq!((f.setters.len(), f.getters.len()), (base.0 + 1, base.1), "a broken pack changes nothing");
    assert!(f.remove_pack("lamps"));
    assert_eq!((f.setters.len(), f.getters.len()), base);
}
