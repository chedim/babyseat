//! The banner port's built-ins (the operator, 2026-11-10: string built-ins as plain functions, no new syntax):
//! lower, strip, startswith, lines, words, join, head; sort_by, fit; localtime.
use membrane_vm::expr;
use serde_json::{json, Value as J};

fn ev(src: &str, env: J) -> J {
    let s = membrane_vm::evaluate_json(src, &env.to_string()).unwrap();
    serde_json::from_str(&s).unwrap()
}

#[test]
fn strings() {
    let e = json!({"t": "  Hello World\nsecond line  "});
    assert_eq!(ev("lower(t)", e.clone()), json!("  hello world\nsecond line  "));
    assert_eq!(ev("strip(t)", e.clone()), json!("Hello World\nsecond line"));
    assert_eq!(ev("startswith(strip(t), 'Hello')", e.clone()), json!(true));
    assert_eq!(ev("lines(strip(t))", e.clone()), json!(["Hello World", "second line"]));
    assert_eq!(ev("words(t)", e.clone()), json!(["Hello", "World", "second", "line"]));
    assert_eq!(ev("join(words(t), ' ')", e.clone()), json!("Hello World second line"));
    assert_eq!(ev("head('abcdef', 3)", e.clone()), json!("abc"));
    assert_eq!(ev("head('ab', 3)", e.clone()), json!("ab"));
    assert_eq!(ev("head('héllo', 2)", e), json!("hé"));                  // characters, not bytes
}

#[test]
fn sort_by_orders_rows_by_a_key() {
    let e = json!({"r": [{"n": "a", "ts": 2}, {"n": "b", "ts": 9}, {"n": "c", "ts": null}, {"n": "d", "ts": 5}]});
    assert_eq!(ev("[prop(x, 'n') for x in sort_by(r, 'ts', True)]", e.clone()), json!(["b", "d", "a", "c"]));
    assert_eq!(ev("[prop(x, 'n') for x in sort_by(r, 'ts', False)]", e), json!(["c", "a", "d", "b"]));
}

#[test]
fn fit_keeps_the_most_important_lines_in_budget_in_page_order() {
    // {text, prio (lower first), slot (page order)}; each line costs len(text) + 1
    let rows = json!([{"text": "aaaa", "prio": 2, "slot": 1}, {"text": "bbbbbbbb", "prio": 0, "slot": 3},
                      {"text": "cc", "prio": 1, "slot": 2}, {"text": "ddddddddd", "prio": 3, "slot": 0}]);
    assert_eq!(ev("fit(r, 16)", json!({"r": rows})), json!(["cc", "bbbbbbbb"]));            // 9 + 3 = 12; aaaa: 17 > 16
    assert_eq!(ev("fit(r, 17)", json!({"r": rows})), json!(["aaaa", "cc", "bbbbbbbb"]));    // exactly the budget: kept
}

#[test]
fn localtime_gives_the_local_calendar() {
    let lt = ev("localtime(1800000000)", json!({}));
    for k in ["year", "month", "day", "hour", "minute", "date", "clock", "zone", "day_number"] {
        assert!(lt.get(k).is_some(), "{k}: {lt}");
    }
    let a = ev("prop(localtime(1800000000), 'day_number')", json!({}));
    let b = ev("prop(localtime(1800000000 + 86400), 'day_number')", json!({}));
    assert_eq!(b.as_i64().unwrap() - a.as_i64().unwrap(), 1);
    let _ = expr::py_str(&expr::V::None);
}

#[test]
fn clip_and_ago_match_the_banner() {
    assert_eq!(ev("clip('  a   b  ', 10)", json!({})), json!("a b"));
    assert_eq!(ev("clip('abcdefghijkl', 8)", json!({})), json!("abcde..."));
    let now = 1_800_000_000.0;
    assert_eq!(ev(&format!("ago({}, {})", now - 60.0, now), json!({})), json!("just now"));
    assert_eq!(ev(&format!("ago({}, {})", now - 600.0, now), json!({})), json!("10 min ago"));
    assert_eq!(ev(&format!("ago({}, {})", now - 7200.0, now), json!({})), json!("2h ago"));
    assert_eq!(ev(&format!("ago({}, {})", now - 30.0 * 86400.0, now), json!({})).as_str().unwrap().len(), 10);  // a date
    assert_eq!(ev("ago(None, 5)", json!({})), json!(""));
}

#[test]
fn fit_shows_a_groups_heading_once_with_its_first_kept_line() {
    let rows = json!([{"text": "- a", "prio": 3, "slot": 501, "head": "DECIDED:", "head_slot": 500},
                      {"text": "- b", "prio": 3, "slot": 502, "head": "DECIDED:", "head_slot": 500},
                      {"text": "x", "prio": 0, "slot": 100}]);
    assert_eq!(ev("fit(r, 100)", json!({"r": rows})), json!(["x", "DECIDED:", "- a", "- b"]));
    assert_eq!(ev("fit(r, 15)", json!({"r": rows})), json!(["x", "DECIDED:", "- a"]));     // 2 + 9 + 4 = 15
    assert_eq!(ev("fit(r, 10, 5)", json!({"r": rows})), json!(["x"]));                     // 5 already used
    assert_eq!(ev("head([1, 2, 3], 2)", json!({})), json!([1, 2]));
    assert_eq!(ev("epoch('2026-11-10T10:00:00Z')", json!({})), json!(1794304800.0));
}

#[test]
fn concat_joins_lists_and_plus_stays_vector_addition() {
    assert_eq!(ev("concat([1], [2, 3], None, ['a'])", json!({})), json!([1, 2, 3, "a"]));
    assert_eq!(ev("[1, 2] + [3, 4]", json!({})), json!([4.0, 6.0]));
}

#[test]
fn unspeak_group_by_distinct_before() {
    assert_eq!(ev("unspeak(t)", json!({"t": "[user#owner: owner]\nship it"})), json!("ship it"));
    assert_eq!(ev("unspeak(t)", json!({"t": "[user: Ann] [HA user: Bo]\nhi"})), json!("hi"));   // as speakers.py: stacked tags
    assert_eq!(ev("unspeak(t)", json!({"t": "[HA user: Bo]\n[user#x: y]\nhi"})), json!("hi"));
    assert_eq!(ev("unspeak(t)", json!({"t": "no speaker"})), json!("no speaker"));
    // the shadow's catch (2026-11-10): a tag followed by text on the same line, as speakers._ID_LINE strips it
    assert_eq!(ev("unspeak(t)", json!({"t": "[user#owner: owner] had more thoughts on 1043"})), json!("had more thoughts on 1043"));
    assert_eq!(ev("unspeak(t)", json!({"t": "[user: Ann]  hi\nthere"})), json!("hi\nthere"));
    let rows = json!([{"k": "a", "v": 1}, {"k": "b", "v": 2}, {"k": "a", "v": 3}]);
    assert_eq!(ev("group_by(r, 'k')", json!({"r": rows})),
               json!([{"key": "a", "rows": [{"k": "a", "v": 1}, {"k": "a", "v": 3}]}, {"key": "b", "rows": [{"k": "b", "v": 2}]}]));
    assert_eq!(ev("distinct([3, 1, 3, 2, 1])", json!({})), json!([3, 1, 2]));
    assert_eq!(ev("before('f:membrane/x.py', ':')", json!({})), json!("f"));
    assert_eq!(ev("before('plain', ':')", json!({})), json!("plain"));
    assert_eq!(ev("after('f:membrane/x.py', ':')", json!({})), json!("membrane/x.py"));
}

#[test]
fn a_long_string_literal_does_not_count_toward_the_character_cap() {
    let q = "x".repeat(900);
    assert_eq!(ev(&format!("len('{}')", q), json!({})), json!(900));
    let long_logic = vec!["a"; 220].join(" + ");                      // ~880 characters of logic: still refused
    assert!(membrane_vm::evaluate_json(&long_logic, "{\"a\": 1}").unwrap_err().contains("at most"));
}

#[test]
fn replace_swaps_every_occurrence() {
    // the About view (operator 2026-11-10): a changelog line's **bold** markers come off before it is shown
    assert_eq!(ev("replace('- **Code** is a **tree**', '**', '')", json!({})), json!("- Code is a tree"));
    assert_eq!(ev("replace('a-b-c', '-', ' ')", json!({})), json!("a b c"));
    assert_eq!(ev("replace('abc', '', 'x')", json!({})), json!("abc"));       // an empty pattern changes nothing
}
