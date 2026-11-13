// GET /ui/fieldvm on the full-set seat (membrane/packs/door_fieldvm.c1f): the browser VM's start snapshot, taken as the page takes it
// (membrane/ui/fieldvm.js `connect()`: every pack of `snap.packs` into the page's field engine, then every entity of `snap.entities`). The page's own
// engine (the wasm core) must accept what the seat sends: the view packs load once `dom` is there, with no error.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const port = 21000 + Math.floor(Math.random() * 1000);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`fieldvm snapshot: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
initSync({ module: readFileSync(path.join(repo, 'membrane/ui/fieldvm_core_bg.wasm')) });
const data = mkdtempSync(path.join(tmpdir(), 'fieldvm-snap-'));
const seat = spawn('sh', [path.join(repo, 'scripts/seat-full'), '--data-dir', path.join(data, 'd'), '--port', String(port)],
  { cwd: repo, stdio: 'ignore', env: { ...process.env, CLIFFVM: bin, CLAUDE_BIN: '/usr/bin/true' } });
try {
  for (let i = 0; i < 100; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/`)).ok) break; } catch {} await sleep(200); }
  let token = '';
  for (let i = 0; i < 50 && !token; i++) { try { token = readFileSync(path.join(data, 'd/owner.token'), 'utf8').trim(); } catch { await sleep(100); } }
  const r = await fetch(`http://127.0.0.1:${port}/ui/fieldvm`, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal(r.status, 200);
  const snap = await r.json();
  assert.ok(snap.packs && typeof snap.packs === 'object' && Array.isArray(snap.entities), 'the shape connect() reads');
  // what connect() does with it: the page's `dom` first (a stub of it here, as tests/js/about_view.test.mjs has), then each pack of the snapshot, then the entities
  const f = new Field();
  f.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n");
  for (const [name, src] of Object.entries(snap.packs)) if (name !== 'goalgraph') f.set_pack(name, src);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the page loads every pack of the snapshot');
  assert.deepEqual(Object.keys(snap.packs), ['about']);
  for (const e of snap.entities) f.load(e.eid, e.cls, JSON.stringify(e.props));
  console.log(`fieldvm snapshot: ok (${Object.keys(snap.packs).length} pack(s): ${Object.keys(snap.packs).join(' ')}; ${snap.entities.length} entities)`);
} finally { seat.kill(); await sleep(300); rmSync(data, { recursive: true, force: true }); }
