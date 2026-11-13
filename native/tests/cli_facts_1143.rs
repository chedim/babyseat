//! `--fact name=value` tells any Boot fact (#1143): the host passes it through, a pack decides what it means. The value is JSON when
//! it parses as JSON (a number, true, a list), else the text as it is. The named options keep winning for the facts they own.
#![cfg(feature = "store")]
use membrane_vm::cli::{facts, parse, Cmd};
use serde_json::json;

fn run(args: &[&str]) -> membrane_vm::cli::RunArgs {
    let a: Vec<String> = args.iter().map(|s| s.to_string()).collect();
    match parse(&a).unwrap() { Cmd::Run(r) => r, o => panic!("{o:?}") }
}

#[test]
fn a_fact_option_becomes_a_boot_fact_json_or_text() {
    let r = run(&["run", "p", "--fact", "chat_max_turns=12", "--fact", "chat_claude_bin=/opt/x/claude", "--fact", "flag=true", "--fact", "list=[1,2]"]);
    let f = facts(&r);
    assert_eq!(f["chat_max_turns"], json!(12));
    assert_eq!(f["chat_claude_bin"], json!("/opt/x/claude"));
    assert_eq!(f["flag"], json!(true));
    assert_eq!(f["list"], json!([1, 2]));
}

#[test]
fn the_named_facts_are_not_overridden_and_a_bad_option_is_refused() {
    let r = run(&["run", "p", "--fact", "seat_name=Eve"]);
    assert_eq!(facts(&r)["seat_name"], json!("Bob"));
    let a: Vec<String> = ["run", "p", "--fact", "novalue"].iter().map(|s| s.to_string()).collect();
    assert!(parse(&a).unwrap_err().contains("--fact"));
    let a: Vec<String> = ["run", "p", "--fact", "=1"].iter().map(|s| s.to_string()).collect();
    assert!(parse(&a).is_err());
}
