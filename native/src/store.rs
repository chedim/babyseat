//! THE STORE DRIVER (the operator, 2026-11-10: "build the sqlite driver c1f pack"): `.c1f` reads the seat's SQLite
//! store. `sql(query, args...)` is a built-in of the engine.
//!
//! ONE SQLITE PER PROCESS (0.7.53, data safety): this module never opens a database. It used to, with a copy of SQLite
//! compiled in (rusqlite "bundled"), on the very WAL file the seat's Python opens with CPython's sqlite3: two SQLite
//! copies in one process on one WAL database (POSIX locks are per process: a close in one copy drops the other's
//! locks, and the -shm/-wal can be truncated under the other's mmap -> SIGBUS in walFindFrame). Every query now runs
//! through an `SqlExec` the host hands in -- in the seat, membrane/sqlread.py on the seat's own sqlite3.
//!
//! - Read-only: the executor opens read-only connections with `query_only` on and refuses a statement that is not a
//!   pure read before it runs. Arguments are bound, never spliced.
//! - As the official drivers (the operator: "if they have limits, match; if not, don't have them"): no time limit and
//!   no row cap; a locked database is waited for up to BUSY_TIMEOUT_MS, the default of Python's sqlite3 and rusqlite.
//! - Can't hang the VM (the operator: "make sure they can't hang the whole vm"): a query never runs under the
//!   engine's lock. `request` starts it on its own thread (the executor's call) and answers `Pending`;
//!   the getter reads None meanwhile and recomputes when the rows arrive (`take_ready`, the engine marks it dirty).
//!   A runaway query ties up only its own thread: a getter asking a different question interrupts its previous one
//!   (the executor's `interrupt`), and `interrupt_all` stops every query (shutdown). In WAL mode a reader never waits
//!   for the seat's writer.
use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use serde_json::Value as J;

pub const BUSY_TIMEOUT_MS: u64 = 5000;  // the drivers' default (Python sqlite3 timeout=5.0, rusqlite 5 s)

/// Runs the store's reads (the host's SQLite: one copy per process). `token` names a running query for `interrupt`.
pub trait SqlExec: Send + Sync {
    fn run(&self, token: u64, sql: &str, args: &[J]) -> Result<Vec<J>, String>;
    fn interrupt(&self, token: u64);
}

/// A query's answer as the engine sees it.
pub enum Poll { Ready(Result<Vec<J>, String>), Pending }

enum State { Pending, Done(Result<Vec<J>, String>) }

#[derive(Default)]
pub struct Shared {
    results: HashMap<String, State>,
    running: HashMap<String, u64>,
    next_token: u64,
    exec: Option<Arc<dyn SqlExec>>,
    /// the query each asker (a getter instance) last asked: asking another interrupts that one
    asked: HashMap<String, String>,
    ready: Vec<String>,
    /// effects run by the drivers (fs, proc): (entity, verb, receipt), taken by the engine's next round
    receipts: Vec<(String, String, J)>,
    effects_running: usize,
    /// a running command's child process, by its entity (Command.stop kills it)
    children: HashMap<String, Arc<Mutex<Option<std::process::Child>>>>,
    done: u64,
}

pub struct Store { pub path: String, pub dir: std::path::PathBuf, sh: Arc<(Mutex<Shared>, Condvar)> }

pub fn key(sql: &str, args: &[J]) -> String { format!("{}\u{0}{}", sql, J::Array(args.to_vec())) }
pub fn jsonl_key(name: &str) -> String { format!("\u{1}jsonl\u{0}{}", name) }
/// Each asker has its own answer: two getters asking the same thing never take each other's (a shared answer is
/// consumed by one and wakes both: they asked again forever -- the 2026-11-10 hang).
pub fn scoped(asker: &str, k: &str) -> String { format!("{}\u{3}{}", asker, k) }

impl Store {
    /// The store at `path` (its label, and its seat folder for jsonl: the folder holding the store's folder), read
    /// through `exec` -- the host's SQLite. Nothing here opens the file.
    pub fn with_exec(path: &str, exec: Arc<dyn SqlExec>) -> Store {
        let dir = std::path::Path::new(path).parent().and_then(|p| p.parent()).map(|p| p.to_path_buf()).unwrap_or_default();
        let st = Store { path: path.to_string(), dir, sh: Arc::new((Mutex::new(Shared::default()), Condvar::new())) };
        st.sh.0.lock().unwrap_or_else(|e| e.into_inner()).exec = Some(exec);
        st
    }

    /// The drivers without a database (fs, proc, jsonl): `sql(...)` then says no store is attached.
    pub fn io_only(dir: &str) -> Store {
        Store { path: String::new(), dir: std::path::PathBuf::from(dir), sh: Arc::new((Mutex::new(Shared::default()), Condvar::new())) }
    }

    fn exec(&self) -> Result<Arc<dyn SqlExec>, String> {
        self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).exec.clone()
            .ok_or_else(|| "sql: no store attached to this field".to_string())
    }

    /// The engine's call: the rows when this query has finished since it was asked (taken: the next ask runs it
    /// again), Pending while it runs; started now when it isn't running. `asker` names who asks (a getter
    /// instance): a different query from the same asker interrupts its previous one.
    pub fn request(&self, asker: &str, sql: &str, args: &[J]) -> Poll {
        let k = scoped(asker, &key(sql, args));
        {
            let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
            match sh.results.get(&k) {
                Some(State::Done(_)) => {
                    if let Some(State::Done(r)) = sh.results.remove(&k) { return Poll::Ready(r); }
                }
                Some(State::Pending) => { sh.asked.insert(asker.to_string(), k); return Poll::Pending; }
                None => {}
            }
            if let Some(prev) = sh.asked.insert(asker.to_string(), k.clone()) {
                if prev != k && !sh.asked.values().any(|v| *v == prev) {
                    // superseded and wanted by no one: stop it, and forget an answer that came in for it
                    if let (Some(t), Some(x)) = (sh.running.get(&prev), sh.exec.as_ref()) { x.interrupt(*t); }
                    if matches!(sh.results.get(&prev), Some(State::Done(_))) { sh.results.remove(&prev); }
                }
            }
            sh.results.insert(k.clone(), State::Pending);
        }
        let exec = match self.exec() { Ok(x) => x, Err(e) => return self.finish(&k, Err(e)) };
        let token = {
            let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
            sh.next_token += 1;
            let t = sh.next_token;
            sh.running.insert(k.clone(), t);
            t
        };
        let (sh, sql, args, k2) = (self.sh.clone(), sql.to_string(), args.to_vec(), k.clone());
        std::thread::Builder::new().name("membrane-sql".into()).spawn(move || {
            let r = exec.run(token, &sql, &args);
            let mut g = sh.0.lock().unwrap_or_else(|e| e.into_inner());
            g.running.remove(&k2);
            if g.asked.values().any(|v| *v == k2) {      // still wanted: its getter recomputes with it
                g.results.insert(k2.clone(), State::Done(r));
                g.ready.push(k2);
            } else {
                g.results.remove(&k2);                    // superseded on the way: dropped, never a stale answer
            }
            g.done += 1;
            sh.1.notify_all();
        }).expect("a thread for a query");
        Poll::Pending
    }

    /// A JSON-lines file of the seat's own folder (the directory holding the store's folder): `name` is a plain file
    /// name ending in .jsonl -- no path, no `..`. Each line a row (a line that isn't JSON is skipped). Asked like a
    /// query: read off the engine's thread, Pending until it's in.
    pub fn request_jsonl(&self, asker: &str, name: &str) -> Poll {
        if name.is_empty() || name.contains('/') || name.contains('\\') || name.contains("..") || !name.ends_with(".jsonl") {
            return Poll::Ready(Err(format!("jsonl: {:?} is not a .jsonl file of this seat (a plain file name)", name)));
        }
        let dir = self.dir.clone();
        if dir.as_os_str().is_empty() { return Poll::Ready(Err("jsonl: no seat folder".into())); }
        let k = scoped(asker, &jsonl_key(name));
        {
            let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
            match sh.results.get(&k) {
                Some(State::Done(_)) => { if let Some(State::Done(r)) = sh.results.remove(&k) { return Poll::Ready(r); } }
                Some(State::Pending) => { sh.asked.insert(asker.to_string(), k); return Poll::Pending; }
                None => {}
            }
            sh.asked.insert(asker.to_string(), k.clone());
            sh.results.insert(k.clone(), State::Pending);
        }
        let (sh, path, k2) = (self.sh.clone(), dir.join(name), k.clone());
        std::thread::Builder::new().name("membrane-jsonl".into()).spawn(move || {
            let r = match std::fs::read_to_string(&path) {
                Ok(t) => Ok(t.lines().filter_map(|l| serde_json::from_str::<J>(l).ok()).collect()),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(vec![]),     // no file yet: no rows
                Err(e) => Err(format!("jsonl {}: {}", path.display(), e)),
            };
            let mut g = sh.0.lock().unwrap_or_else(|e| e.into_inner());
            if g.asked.values().any(|v| *v == k2) { g.results.insert(k2.clone(), State::Done(r)); g.ready.push(k2); }
            else { g.results.remove(&k2); }
            g.done += 1;
            sh.1.notify_all();
        }).expect("a thread for a file");
        Poll::Pending
    }

    /// A read on its own thread, asked like a query (`key` names it; the same asker asking another key drops the
    /// first's answer). The fs built-ins (read, listdir, stat) go through here.
    /// `keep`: the answer stays for this asker until it asks another key (or `forget`): a getter that asks again with
    /// the same arguments reads it again instead of asking anew (a chain of slow calls in one getter settles).
    pub fn request_job(&self, asker: &str, k: String, keep: bool, job: Box<dyn FnOnce() -> Result<Vec<J>, String> + Send>) -> Poll {
        let k = scoped(asker, &k);
        {
            let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
            match sh.results.get(&k) {
                Some(State::Done(r)) if keep => { let r = r.clone(); sh.asked.insert(asker.to_string(), k); return Poll::Ready(r); }
                Some(State::Done(_)) => { if let Some(State::Done(r)) = sh.results.remove(&k) { return Poll::Ready(r); } }
                Some(State::Pending) => { sh.asked.insert(asker.to_string(), k); return Poll::Pending; }
                None => {}
            }
            if let Some(prev) = sh.asked.insert(asker.to_string(), k.clone()) {
                if prev != k && !sh.asked.values().any(|v| *v == prev) && matches!(sh.results.get(&prev), Some(State::Done(_))) {
                    sh.results.remove(&prev);
                }
            }
            sh.results.insert(k.clone(), State::Pending);
        }
        let (sh, k2) = (self.sh.clone(), k);
        std::thread::Builder::new().name("membrane-io".into()).spawn(move || {
            let r = job();
            let mut g = sh.0.lock().unwrap_or_else(|e| e.into_inner());
            if g.asked.values().any(|v| *v == k2) { g.results.insert(k2.clone(), State::Done(r)); g.ready.push(k2); }
            else { g.results.remove(&k2); }
            g.done += 1;
            sh.1.notify_all();
        }).expect("a thread for a read");
        Poll::Pending
    }

    /// An effect (a driver's verb, written by a person, the model or a setter): run on its own thread; its receipt
    /// lands on the entity as `<verb>_result` at the engine's next round. Never under the engine's lock.
    pub fn effect(&self, eid: &str, verb: &str, job: Box<dyn FnOnce(&Arc<(Mutex<Shared>, Condvar)>, &str) -> J + Send>) {
        self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).effects_running += 1;
        let (sh, eid, verb) = (self.sh.clone(), eid.to_string(), verb.to_string());
        std::thread::Builder::new().name("membrane-effect".into()).spawn(move || {
            let rec = job(&sh, &eid);
            let mut g = sh.0.lock().unwrap_or_else(|e| e.into_inner());
            g.effects_running -= 1;
            g.receipts.push((eid, verb, rec));
            g.done += 1;
            sh.1.notify_all();
        }).expect("a thread for an effect");
    }

    /// Drop the kept answers of every asker whose name starts with `prefix` (a banner build starts fresh); a job
    /// still running finishes and its answer is dropped.
    pub fn forget(&self, prefix: &str) {
        let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
        let gone: Vec<(String, String)> = sh.asked.iter().filter(|(a, _)| a.starts_with(prefix)).map(|(a, k)| (a.clone(), k.clone())).collect();
        for (a, k) in gone {
            sh.asked.remove(&a);
            if !sh.asked.values().any(|v| *v == k) && matches!(sh.results.get(&k), Some(State::Done(_))) { sh.results.remove(&k); }
        }
    }

    /// The effects' receipts since the last round.
    pub fn take_receipts(&self) -> Vec<(String, String, J)> {
        std::mem::take(&mut self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).receipts)
    }

    /// Stop a running command (Command.stop): its child is killed; its run's receipt says so.
    pub fn kill(&self, eid: &str) -> bool {
        let c = self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).children.get(eid).cloned();
        match c { Some(c) => c.lock().unwrap_or_else(|e| e.into_inner()).as_mut().map(|ch| ch.kill().is_ok()).unwrap_or(false), None => false }
    }

    fn finish(&self, k: &str, r: Result<Vec<J>, String>) -> Poll {
        self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).results.remove(k);
        Poll::Ready(r)
    }

    /// The queries that finished since the last call (the engine marks their getters dirty).
    pub fn take_ready(&self) -> Vec<String> { std::mem::take(&mut self.sh.0.lock().unwrap_or_else(|e| e.into_inner()).ready) }

    /// How many queries are running.
    pub fn pending(&self) -> usize { outstanding(&self.sh.0.lock().unwrap_or_else(|e| e.into_inner())) }

    /// Wait until a query finishes (or `timeout`); true when one did. Called without the engine's lock.
    pub fn wait(&self, timeout: Option<Duration>) -> bool {
        let g = self.sh.0.lock().unwrap_or_else(|e| e.into_inner());
        if !g.ready.is_empty() { return true; }
        let seen = g.done;
        match timeout {
            Some(t) => { let (g, _) = self.sh.1.wait_timeout_while(g, t, |s| s.done == seen).unwrap_or_else(|e| e.into_inner()); g.done != seen }
            None => { let g = self.sh.1.wait_while(g, |s| s.done == seen).unwrap_or_else(|e| e.into_inner()); g.done != seen }
        }
    }

    /// Stop every running query (shutdown).
    pub fn interrupt_all(&self) { interrupt_all(&self.sh) }

    /// A handle that waits and interrupts without the engine (the binding's waiter thread holds one).
    pub fn waiter(&self) -> Waiter { Waiter { sh: self.sh.clone() } }

    /// Run one read now, on this thread (tests and tools; the engine uses `request`).
    pub fn query(&self, sql: &str, args: &[J]) -> Result<Vec<J>, String> {
        let exec = self.exec()?;
        let token = { let mut sh = self.sh.0.lock().unwrap_or_else(|e| e.into_inner()); sh.next_token += 1; sh.next_token };
        exec.run(token, sql, args)
    }
}

/// Waits and interrupts from outside the engine's lock.
#[derive(Clone)]
pub struct Waiter { sh: Arc<(Mutex<Shared>, Condvar)> }
impl Waiter {
    pub fn forget(&self, prefix: &str) { Store { path: String::new(), dir: Default::default(), sh: self.sh.clone() }.forget(prefix) }
    pub fn wait(&self, timeout: Option<Duration>) -> bool {
        Store { path: String::new(), dir: Default::default(), sh: self.sh.clone() }.wait(timeout)
    }
    pub fn pending(&self) -> usize { outstanding(&self.sh.0.lock().unwrap_or_else(|e| e.into_inner())) }
    pub fn interrupt_all(&self) { interrupt_all(&self.sh) }
}

fn interrupt_all(sh: &Arc<(Mutex<Shared>, Condvar)>) {
    let g = sh.0.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(x) = g.exec.as_ref() { for t in g.running.values() { x.interrupt(*t); } }
}

/// Queries, file reads and effects not landed yet: still running, or finished with their rows not taken by a round (the
/// engine takes them at its next round) -- so a waiter never stops between a read finishing and its rows landing.
fn outstanding(sh: &Shared) -> usize {
    sh.results.values().filter(|v| matches!(v, State::Pending)).count() + sh.ready.len() + sh.effects_running + sh.receipts.len()
}

/// The drivers' access to a running command's slot (proc.rs).
pub(crate) fn child_slot(sh: &Arc<(Mutex<Shared>, Condvar)>, eid: &str) -> Arc<Mutex<Option<std::process::Child>>> {
    let slot = Arc::new(Mutex::new(None));
    sh.0.lock().unwrap_or_else(|e| e.into_inner()).children.insert(eid.to_string(), slot.clone());
    slot
}
pub(crate) fn child_done(sh: &Arc<(Mutex<Shared>, Condvar)>, eid: &str) {
    sh.0.lock().unwrap_or_else(|e| e.into_inner()).children.remove(eid);
}
pub(crate) type Sh = Arc<(Mutex<Shared>, Condvar)>;
