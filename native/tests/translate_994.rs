//! #994 row 9: the WHEN/WHERE/WRITE translation layer. goalgraph's triggers (data, packs/goalgraph.json) translate to the
//! fields row 2 wrote by hand; the whole-field checksum gate is parity_994.rs (its goalgraph run now uses the translation).
use membrane_vm::translate::{goalgraph_counts, goalgraph_setters, specs_of, translate, Field, GOALGRAPH};
use serde_json::json;

fn w(t: &str, p: &str, v: &str) -> (String, String, String) { (t.into(), p.into(), v.into()) }
const PLAN: &str = "{'about': x.eid, 'prompt': 'plan the goal: ' + x.replan, 'lane': 'planner'}";

#[test]
fn goalgraph_translates_to_the_row_2_setters() {
    let got: Vec<_> = goalgraph_setters().into_iter().map(|s| (s.name, s.cls, s.prop, s.writes)).collect();
    let arm = vec![w("x", "status", "'armed'"), w("x", "armed_since", "clock")];
    let want = vec![
        ("ready", "Task", "open_blockers", vec![w("x", "status", "'owed'")]),
        ("ready-on-status", "Task", "status", vec![w("x", "status", "'owed'")]),
        ("replan-task", "Task", "status", vec![w("x.goal", "replan", "'task ' + x.eid + ' ' + x.status")]),
        ("replan-card", "Decision", "status", vec![w("x.goal", "replan", "'card ' + x.eid + ' ' + x.status")]),
        ("wait-armed", "Wait", "open_blockers", arm.clone()),
        ("wait-armed-on-status", "Wait", "status", arm),
        ("wait-check", "Wait", "due", vec![w("x", "check", "'due'")]),
        ("plan", "Goal", "replan", vec![w("membrane::llm.call", "", PLAN)]),
        ("plan-after-reopen", "Goal", "status", vec![w("membrane::llm.call", "", PLAN)]),
    ];
    let want: Vec<_> = want.into_iter().map(|(n, c, p, wr)| (n.to_string(), c.to_string(), p.to_string(), wr)).collect();
    assert_eq!(got, want);
}

#[test]
fn conditions_are_translated_not_copied() {
    let m: std::collections::HashMap<_, _> = goalgraph_setters().into_iter().map(|s| (s.name.clone(), s.when)).collect();
    assert_eq!(m["ready"], "x.open_blockers < 0.5 and x.status == 'open'");
    assert_eq!(m["wait-check"], "x.due");
    assert_eq!(m["replan-task"], "(x.status == 'done' or x.status == 'failed' or x.status == 'stuck')");
    assert_eq!(m["plan"], "(x.status or 'open') == 'open' and x.replan");
}

#[test]
fn the_five_blocker_triggers_are_the_counting_getter() {
    let mut c = goalgraph_counts();
    c.sort();
    let t = |a: &str, b: &str, c: &str| (a.to_string(), b.to_string(), c.to_string());
    assert_eq!(c, vec![t("Decision", "closed", "Task"), t("Decision", "closed", "Wait"), t("Task", "done", "Task"), t("Task", "done", "Wait"), t("Wait", "done", "Task")]);
}

#[test]
fn every_goalgraph_trigger_has_a_field_form() {
    for (n, sp) in specs_of(GOALGRAPH) { assert!(translate(&n, &sp).is_ok(), "{}", n); }
    assert_eq!(specs_of(GOALGRAPH).len(), 12);
}

#[test]
fn a_spec_with_no_field_form_is_refused_not_guessed() {
    let mut sp = specs_of(GOALGRAPH).into_iter().find(|(n, _)| n == "ready").unwrap().1;
    sp.when.push(vec![json!("near"), json!("?t"), json!("?u"), json!(1)]);
    assert!(translate("x", &sp).unwrap_err().contains("WHEN near"));
    let mut sp = specs_of(GOALGRAPH).into_iter().find(|(n, _)| n == "ready").unwrap().1;
    sp.wh.push(vec![json!("pos"), json!("?t")]);
    assert!(matches!(translate("x", &sp), Err(_)));
    assert!(matches!(translate("ok", &specs_of(GOALGRAPH)[2].1), Ok(Field::Set(_))));
}
