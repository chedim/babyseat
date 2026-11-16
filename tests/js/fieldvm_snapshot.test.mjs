// GET /ui/fieldvm on the release seat (scripts/seat) (membrane/packs/door_fieldvm.c1f): the browser VM's start snapshot, taken as the page takes it
// (membrane/apps/barest.c1f/ui/fieldvm.js `connect()`: every pack of `snap.packs` into the page's field engine, then every entity of `snap.entities`). The page's own
// engine (the wasm core) must accept what the seat sends: the view packs load once `dom` is there, with no error.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const port = 21000 + Math.floor(Math.random() * 1000);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`fieldvm snapshot: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
initSync({ module: readFileSync(path.join(repo, 'membrane/apps/barest.c1f/ui/fieldvm_core_bg.wasm')) });
const data = mkdtempSync(path.join(tmpdir(), 'fieldvm-snap-'));
const seat = spawn('sh', [path.join(repo, 'scripts/seat'), '--data-dir', path.join(data, 'd'), '--port', String(port)],
  { cwd: repo, stdio: 'ignore', env: { ...process.env, CLIFFVM: bin, CLAUDE_BIN: '/usr/bin/true' } });
try {
  for (let i = 0; i < 100; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/`)).ok) break; } catch {} await sleep(200); }
  let token = '';
  for (let i = 0; i < 50 && !token; i++) { try { token = readFileSync(path.join(data, 'd/admin.token'), 'utf8').trim(); } catch { await sleep(100); } }
  const r = await fetch(`http://127.0.0.1:${port}/ui/fieldvm`, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal(r.status, 200);
  const snap = await r.json();
  assert.ok(snap.packs && typeof snap.packs === 'object' && Array.isArray(snap.entities), 'the shape connect() reads');
  // what connect() does with it: the page's `dom` first (a stub of it here, as tests/js/about_view.test.mjs has), then each pack of the snapshot, then the entities
  const f = new Field(); const errs = {};
  f.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n");
  // the app folder's packs that import `dom` (a pack that imports another loads after it)
  f.set_pack('fetch', "PACK fetch 1\nFetch.request getter: None\n");   // the page's own Fetch host pack, stubbed
  // the real loader (ui/boot.js `loadPacks`): the packs of the snapshot in the snapshot's order; a pack whose IMPORT is not loaded yet is refused and TRIED AGAIN in the next round, until a round loads none more
  const load = (names) => {
    let todo = names;
    for (let more = true; more && todo.length;) {
      const left = [];
      for (const name of todo) { try { f.set_pack(name, snap.packs[name]); delete errs[name]; } catch (e) { errs[name] = String(e); left.push(name); } }
      more = left.length < todo.length; todo = left;
    }
  };
  const order = Object.keys(snap.packs);
  // the order must not matter: the seat's own order (alphabetical, so a view comes before the shell it imports, and the first-run window before the settings view it imports), then the reverse
  load(order);
  assert.deepEqual(errs, {}, `the page loads every pack of the snapshot (${order.join(' ')})`);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'and the engine holds no pack error');
  assert.ok(order.indexOf('firstrun_view') < order.indexOf('settings_view'), 'this snapshot sends the first-run window BEFORE the settings view it imports (the retry is what loads it)');
  const g = new Field();
  g.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n"); g.set_pack('fetch', "PACK fetch 1\nFetch.request getter: None\n");
  { let todo = [...order].reverse(); for (let more = true; more && todo.length;) { const left = []; for (const n of todo) { try { g.set_pack(n, snap.packs[n]); } catch { left.push(n); } } more = left.length < todo.length; todo = left; } }
  assert.deepEqual(JSON.parse(g.pack_errors()), {}, 'the reverse order loads too');
  assert.ok('shell_view' in snap.packs && 'chat_view' in snap.packs, 'the view packs of the app folder');
  assert.equal(snap.views, undefined, 'no per-view switch any more');
  for (const e of snap.entities) f.load(e.eid, e.cls, JSON.stringify(e.props));
  console.log(`fieldvm snapshot: ok (${Object.keys(snap.packs).length} pack(s): ${Object.keys(snap.packs).join(' ')}; ${snap.entities.length} entities)`);
} finally { seat.kill(); await sleep(300); rmSync(data, { recursive: true, force: true }); }
