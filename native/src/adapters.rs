//! #994 row 5: calls as writes (membrane/adapters.py `_written_call`, #959/#956) as on-setters, native VM.
//! A Method entity (class `Method`, prop `method_name`) is written `invoke = {object, call, request: {id, args}}`
//! (a dispatch trigger's write for a request on `<object>.<call>`). The target-side setter decides, purely from
//! the write and the field: an empty request does nothing; a refused / malformed / secret-carrying one writes a
//! refusal receipt (`<call>_result` = {id, status, rc, head}, `<call>_rc`) and clears the call path; a good one
//! becomes an EFFECT (`Fields.method_calls`, like the llm call) whose receipt the host delivers with
//! `Fields::deliver`. The invoke slot is consumed on every path. Same output as the python.
use serde_json::{json, Map, Value as J};

use crate::vm::Core;

pub const CLASS_METHOD: &str = "Method";
/// the executor itself is never called through a write (N.direct_exec_refusal)
const EXECUTORS: [&str; 2] = ["tool:executor", "remote_executor"];
const SECRET: [&str; 7] = ["token", "key", "passw", "secret", "auth", "cookie", "credential"];

fn is_secret(k: &str) -> bool { let l = k.to_lowercase(); SECRET.iter().any(|s| l.contains(s)) }

/// Secret-named keys anywhere in a call's arguments, as dotted paths (`conf.password`).
pub fn secret_args(v: &J, path: &str) -> Vec<String> {
    let mut out = vec![];
    match v {
        J::Object(m) => for (k, x) in m {
            let p = if path.is_empty() { k.clone() } else { format!("{}.{}", path, k) };
            if is_secret(k) { out.push(p) } else { out.extend(secret_args(x, &p)) }
        },
        J::Array(a) => for x in a { out.extend(secret_args(x, path)) },
        _ => {}
    }
    out
}

/// What the setter decided for one `invoke` write.
#[derive(Debug, PartialEq)]
pub enum Verdict {
    /// nothing to do (an empty request keeps the last receipt)
    Nothing,
    /// a refusal: clear the call path (it may hold a secret) or not, and write this receipt
    Refuse { clear: bool, rid: String, head: String },
    /// run it: the effect
    Run { object: String, method: String, call: String, rid: String, args: J },
}

pub fn decide(method_name: Option<&str>, new: &J) -> Verdict {
    let o = |k: &str| new.get(k).and_then(|v| v.as_str()).unwrap_or("");
    let (obj, call, req) = (o("object"), o("call"), new.get("request").cloned().unwrap_or(J::Null));
    let empty = req.is_null() || req == json!("");
    let Some(name) = method_name.filter(|n| !n.is_empty()) else { return Verdict::Nothing };
    if obj.is_empty() || !call.starts_with("call:") || empty { return Verdict::Nothing }
    let rid = req.get("id").map(|i| match i { J::String(s) => s.clone(), J::Null => String::new(), x => x.to_string() }).unwrap_or_default();
    if let Some(why) = req.get("refused").filter(|r| req.is_object() && !(r.is_null() || **r == json!("") || **r == json!(false) || **r == json!(0))) {
        let w = match why { J::String(s) => s.clone(), x => x.to_string() };
        return Verdict::Refuse { clear: true, rid, head: format!("refused: {}: nothing ran", w) };
    }
    let args_ok = req.get("args").map(|a| a.is_object()).unwrap_or(true);
    if !req.is_object() || rid.trim().is_empty() || !args_ok {
        return Verdict::Refuse { clear: true, rid, head: "a call request is {id, args}: nothing ran".into() };
    }
    let args = req.get("args").cloned().filter(|a| !a.is_null()).unwrap_or(json!({}));
    let secret = secret_args(&args, "");
    if !secret.is_empty() {
        return Verdict::Refuse { clear: true, rid, head: format!("refused: a written call can't carry secret-named args ({}); pass secrets through the model's own call instead", secret.join(", ")) };
    }
    if EXECUTORS.contains(&obj) {
        return Verdict::Refuse { clear: false, rid, head: format!("refused: {} is the executor: run code instead", obj) };
    }
    Verdict::Run { object: obj.into(), method: name.into(), call: call.into(), rid, args }
}

/// The receipt props for a call: `<call>_result` and `<call>_rc` (head capped at 4000 chars).
pub fn receipt(call: &str, rid: &str, rc: i64, head: &str, result: Option<J>) -> Vec<(String, J)> {
    let mut rec = Map::new();
    rec.insert("id".into(), json!(rid));
    rec.insert("status".into(), json!(if rc == 0 { "ok" } else { "error" }));
    rec.insert("rc".into(), json!(rc));
    rec.insert("head".into(), json!(head.chars().take(4000).collect::<String>()));
    if let Some(r) = result.filter(|r| !(r.is_null() || r == &json!("") || r == &json!({}) || r == &json!([]) || r == &json!(false) || r == &json!(0))) { rec.insert("result".into(), r); }
    vec![(format!("{}_result", call), J::Object(rec)), (format!("{}_rc", call), json!(rc))]
}

pub fn is_method(core: &Core, eid: &str) -> bool { core.docs.get(eid).map(|e| e.cls == CLASS_METHOD).unwrap_or(false) }

pub fn method_name(core: &Core, eid: &str) -> Option<String> {
    core.docs.get(eid).and_then(|e| e.props.get("method_name")).and_then(|v| v.as_str()).map(String::from)
}
