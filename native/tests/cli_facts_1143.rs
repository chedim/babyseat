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

#[test]
fn several_apps_are_layers_the_first_is_the_root_and_the_door_is_told_the_folders_top_layer_first() {
    let d = std::env::temp_dir().join(format!("cli-apps-{}", std::process::id()));
    let (a, b) = (d.join("a.c1f"), d.join("b.c1f"));
    std::fs::create_dir_all(&a).unwrap();
    std::fs::create_dir_all(&b).unwrap();
    std::fs::write(d.join("boot.c1f"), "PACK boot 1\n").unwrap();
    let (fb, sa, sb) = (d.join("boot.c1f"), a.to_str().unwrap().to_string(), b.to_str().unwrap().to_string());
    let r = run(&["run", fb.to_str().unwrap(), &sa, &sb, "--ui-dir", &sa]);
    assert_eq!(r.apps, [fb.to_str().unwrap(), sa.as_str(), sb.as_str()], "the order given: the first is the root of trust");
    let f = facts(&r);
    let canon = |p: &std::path::Path| std::fs::canonicalize(p).unwrap().to_string_lossy().to_string();
    assert_eq!(f["app_dirs"], json!([canon(&b), canon(&a)]), "the folders only, the later app (the upper layer) first");
    assert!(parse(&["run".to_string()]).is_err(), "run needs an app");
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn the_launcher_tells_only_whether_the_anthropic_key_is_set_never_the_key_and_an_option_cannot_fake_it() {
    // (the environment of THIS process is the one `facts` reads: set, read, restore, in one test so nothing else races on it)
    let r = run(&["run", "p", "--fact", "anthropic_key_set=true"]);
    let old = std::env::var("ANTHROPIC_API_KEY").ok();
    std::env::remove_var("ANTHROPIC_API_KEY");
    assert_eq!(facts(&r)["anthropic_key_set"], json!(false), "no key in the environment: the option cannot say yes");
    std::env::set_var("ANTHROPIC_API_KEY", "");
    assert_eq!(facts(&r)["anthropic_key_set"], json!(false), "an empty variable is no key");
    std::env::set_var("ANTHROPIC_API_KEY", "sk-ant-api03-PLANTED-cli-facts");
    let f = facts(&r);
    assert_eq!(f["anthropic_key_set"], json!(true));
    assert!(!serde_json::to_string(&f).unwrap().contains("PLANTED-cli-facts"), "the key is in no fact");
    match old { Some(v) => std::env::set_var("ANTHROPIC_API_KEY", v), None => std::env::remove_var("ANTHROPIC_API_KEY") }
}
