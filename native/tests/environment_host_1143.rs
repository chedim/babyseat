//! `Environment:host` (#1143, operator 2026-11-14: "Environment node that holds envvars as its properties"): at the seat's start the host writes ONE entity whose PROPERTIES ARE the environment variables
//! (`Environment:host.HOME`, `.MEMBRANE_DATA_DIR`; a variable that is not set is absent). Only an ALLOW-LISTED set carries its value; a secret-bearing name (KEY, TOKEN, SECRET, PASSWORD) reads the text `set`
//! and never the value; `env_allow` extends the list and a secret-looking name is refused with the reason. A STORED entity (class `Environment`, RESIDENT) written by a JOURNALLED outside write.
//! Tests assert content, never the clock.
use membrane_vm::host::{boot_environment, boot_started, environment_props, Host, Line, Options};
use serde_json::{json, Map, Value as J};

fn repo() -> std::path::PathBuf { std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").canonicalize().unwrap() }
fn pack(n: &str) -> String { std::fs::read_to_string(repo().join(format!("membrane/packs/{n}.c1f"))).unwrap_or_else(|e| panic!("{n}.c1f: {e}")) }
fn env(v: &[(&str, &str)]) -> Vec<(String, String)> { v.iter().map(|(k, x)| (k.to_string(), x.to_string())).collect() }
fn facts(v: &[(&str, J)]) -> Map<String, J> { v.iter().map(|(k, x)| (k.to_string(), x.clone())).collect() }
fn tmp(tag: &str) -> std::path::PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let d = std::env::temp_dir().join(format!("environment-{tag}-{}-{n}", std::process::id()));
    std::fs::create_dir_all(&d).unwrap();
    d
}
fn host(store: Option<&std::path::Path>) -> Host {
    let h = Host::open(Options::new(store.map(|p| p.to_string_lossy().to_string()))).unwrap();
    h.load_pack("environment", &pack("environment")).unwrap();
    h.load_stored_entities();
    h.rebuild();
    h
}
fn read(h: &Host, p: &str) -> J { h.get("Environment:host", p) }
fn props(h: &Host) -> Map<String, J> { h.snapshot()["Environment:host"]["props"].as_object().unwrap().clone() }
fn all_text(h: &Host) -> String { h.snapshot().values().map(|v| v.to_string()).collect() }

#[test]
fn the_default_allow_list_carries_its_values_and_every_other_variable_is_absent() {
    let h = host(None);
    boot_environment(&h, &facts(&[]), &env(&[("HOME", "/home/ann"), ("LANG", "en_US.UTF-8"), ("MEMBRANE_DATA_DIR", "/x"), ("CLAUDE_BIN", "/opt/claude"), ("TMPDIR", "/tmp/t"), ("XDG_DATA_HOME", "/d"),
        ("XDG_CONFIG_HOME", "/c"), ("USER", "ann"), ("PATH", "/usr/bin:/bin"), ("EDITOR", "vi")]));
    assert_eq!(read(&h, "MEMBRANE_DATA_DIR"), json!("/x"));
    assert_eq!(read(&h, "HOME"), json!("/home/ann"));
    for k in ["LANG", "CLAUDE_BIN", "TMPDIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "USER"] { assert!(!read(&h, k).is_null(), "{k} is on the default list"); }
    for k in ["PATH", "EDITOR"] { assert!(read(&h, k).is_null(), "{k} is not on the default list: absent"); }
    assert_eq!(props(&h).keys().filter(|k| *k != "parents").count(), 8);
}

#[test]
fn a_variable_that_is_not_set_reads_absent_and_a_pack_reads_the_names_as_written() {
    let h = host(None);
    boot_environment(&h, &facts(&[]), &env(&[("HOME", "/h")]));
    assert!(read(&h, "LANG").is_null());
    h.load_pack("envuse", "PACK envuse 1\nProbe.dir getter: Environment:host.MEMBRANE_DATA_DIR or 'none'\nProbe.home getter: Environment:host.HOME\nProbe.v2 getter: prop('Environment:host', 'X_2') or 'no'\n").unwrap();
    h.born("Probe:a", "Probe", Map::new(), "person");
    h.pump();
    assert_eq!(h.get("Probe:a", "dir"), json!("none"));
    assert_eq!(h.get("Probe:a", "home"), json!("/h"));
    assert_eq!(h.get("Probe:a", "v2"), json!("no"));
}

#[test]
fn a_secret_bearing_variable_reads_the_text_set_never_its_value_and_an_unset_or_empty_one_is_absent() {
    let h = host(None);
    boot_environment(&h, &facts(&[]), &env(&[("ANTHROPIC_API_KEY", "sk-secret-w115"), ("FOO_TOKEN", "xyz-w115"), ("DB_PASSWORD", "pw-w115"), ("MY_SECRET", "s-w115"), ("api_key", "lower-w115"), ("DB_PASSWD", "pd-w115"), ("AWS_CREDENTIALS", "cr-w115"), ("EMPTY_TOKEN", ""), ("HOME", "/h")]));
    for k in ["ANTHROPIC_API_KEY", "FOO_TOKEN", "DB_PASSWORD", "MY_SECRET", "api_key", "DB_PASSWD", "AWS_CREDENTIALS"] { assert_eq!(read(&h, k), json!("set"), "{k}"); }
    assert!(read(&h, "EMPTY_TOKEN").is_null() && read(&h, "OTHER_TOKEN").is_null(), "empty or unset: absent");
    let all = all_text(&h);
    for v in ["sk-secret-w115", "xyz-w115", "pw-w115", "s-w115", "lower-w115", "pd-w115", "cr-w115"] { assert!(!all.contains(v), "{v} appears in the field"); }
}

#[test]
fn env_allow_adds_a_harmless_name_as_a_list_or_as_comma_text_and_a_secret_name_is_refused_with_the_reason() {
    for allow in [json!("FOO_BAR"), json!(["FOO_BAR"]), json!("FOO_BAR, OTHER_ONE"), json!(["FOO_BAR", "OTHER_ONE"])] {
        let h = host(None);
        let said = boot_environment(&h, &facts(&[("env_allow", allow.clone())]), &env(&[("FOO_BAR", "bar-w115"), ("OTHER_ONE", "o"), ("NOT_LISTED", "n")]));
        assert_eq!(read(&h, "FOO_BAR"), json!("bar-w115"), "{allow}");
        assert!(read(&h, "NOT_LISTED").is_null());
        assert!(said.is_empty(), "{allow}: nothing refused");
    }
    let h = host(None);
    let said = boot_environment(&h, &facts(&[("env_allow", json!(["FOO_TOKEN", "FOO_BAR", "my_Secret_x", "DB_PASSWORD", "SSH_KEY"]))]), &env(&[("FOO_TOKEN", "xyz-w115"), ("FOO_BAR", "b"), ("my_Secret_x", "s2-w115"), ("DB_PASSWORD", "pw-w115"), ("SSH_KEY", "k-w115")]));
    assert_eq!(read(&h, "FOO_BAR"), json!("b"));
    assert_eq!(said.len(), 4, "{said:?}");
    for k in ["FOO_TOKEN", "my_Secret_x", "DB_PASSWORD", "SSH_KEY"] {
        assert_eq!(read(&h, k), json!("set"), "{k} is refused its value: only presence");
        let line = said.iter().find(|l| l.contains(k)).unwrap_or_else(|| panic!("{k}: no refusal in {said:?}"));
        assert!(line.contains("KEY") && line.contains("TOKEN") && line.contains("SECRET") && line.contains("PASSWORD") && line.contains("PASSWD") && line.contains("CREDENTIAL"), "the reason names the rule: {line}");
    }
    let all = format!("{} {said:?}", all_text(&h));
    for v in ["xyz-w115", "s2-w115", "pw-w115", "k-w115"] { assert!(!all.contains(v), "{v} appears"); }
}

#[test]
fn the_pure_rule_is_the_same_as_the_boot() {
    let (p, r) = environment_props(&facts(&[]), &env(&[("HOME", "/h"), ("X_TOKEN", "v")]));
    assert_eq!(p["HOME"], json!("/h"));
    assert_eq!(p["X_TOKEN"], json!("set"));
    assert!(r.is_empty());
}

#[test]
fn no_pack_that_declares_the_class_means_nothing_is_written() {
    let h = Host::open(Options::new(None)).unwrap();
    h.rebuild();
    boot_environment(&h, &facts(&[]), &env(&[("HOME", "/h")]));
    assert!(!h.snapshot().contains_key("Environment:host"), "a quiet host writes nothing");
}

#[test]
fn a_replay_restores_the_value_and_a_restart_with_another_environment_shows_the_new_one_and_drops_what_is_no_longer_set_while_the_journal_holds_both() {
    let d = tmp("replay");
    let store = d.join("a.sqlite");
    {
        let h = host(Some(&store));
        boot_environment(&h, &facts(&[]), &env(&[("MEMBRANE_DATA_DIR", "/first"), ("HOME", "/h"), ("LANG", "C")]));
        boot_started(&h, &facts(&[]), true);
        h.pump();
        let whole = h.journal_stored().unwrap();
        let fresh = host(None);
        fresh.replay(&whole);
        fresh.pump();
        assert_eq!(read(&fresh, "MEMBRANE_DATA_DIR"), json!("/first"), "a replay of the run's journal ends with the value the boot saw");
        assert_eq!(read(&fresh, "LANG"), json!("C"));
        let j = format!("{:?}", h.journal_stored().unwrap().iter().filter(|l| format!("{l:?}").contains("Environment:host")).collect::<Vec<_>>());
        assert!(j.contains("/first") && j.contains("host:environment"), "the first boot's value is in its journal as an outside write: {j}");
    }
    {
        let h = host(Some(&store));
        assert_eq!(read(&h, "MEMBRANE_DATA_DIR"), json!("/first"), "the stored row comes back at the restart");
        boot_environment(&h, &facts(&[]), &env(&[("MEMBRANE_DATA_DIR", "/second"), ("HOME", "/h")]));
        h.pump();
        assert_eq!(read(&h, "MEMBRANE_DATA_DIR"), json!("/second"), "the NEW boot value");
        assert!(read(&h, "LANG").is_null(), "LANG is no longer set: absent, not the old value");
        let j = h.journal_stored().unwrap();
        let mine: Vec<&Line> = j.iter().filter(|l| format!("{l:?}").contains("Environment:host")).collect();
        let text = format!("{mine:?}");
        assert!(text.contains("/first") && text.contains("/second") && text.contains("Null"), "the journal holds both boots' writes, the cleared LANG included: {text}");
        assert!(mine.iter().all(|l| match l { Line::Write { writer, .. } | Line::Born { writer, .. } => writer.starts_with("host:") }), "every one is an OUTSIDE write");
        // a replay of the WHOLE stored journal (both boots) ends at the last boot's value
        let fresh = host(None);
        fresh.replay(&j);
        fresh.pump();
        assert_eq!(read(&fresh, "MEMBRANE_DATA_DIR"), json!("/second"), "a replay ends at the last boot's value");
        assert!(read(&fresh, "LANG").is_null());
    }
    let _ = std::fs::remove_dir_all(d);
}

#[test]
fn the_pack_is_in_the_barest_set() {
    let list = std::fs::read_to_string(repo().join("membrane/apps/barest.packs")).unwrap();
    assert!(list.lines().any(|l| l.trim() == "environment"), "scripts/seat and the seat tests load the barest set");
}
