// The field views on the full-set seat's EMPTY-field answers (membrane/packs/door_map.c1f, docs/release-parity.md): the page's OWN code (membrane/ui/field.js, the
// 2D panel and the 3D window's loader; membrane/ui/field3d.js, the projection loader and Blurp's overlay) is run in node against the real seat's real answers, and
// must end on "the field is empty" instead of an error. Nothing is drawn (no DOM, no WebGL): the classes are the page's, their drawing methods are stubs.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import vm from 'node:vm';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const port = 20000 + Math.floor(Math.random() * 1000);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`field empty: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
const data = mkdtempSync(path.join(tmpdir(), 'field-empty-'));
const seat = spawn('sh', [path.join(repo, 'scripts/seat-full'), '--data-dir', path.join(data, 'd'), '--port', String(port)],
  { cwd: repo, stdio: 'ignore', env: { ...process.env, CLIFFVM: bin, CLAUDE_BIN: '/usr/bin/true' } });
try {
  for (let i = 0; i < 100; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/`)).ok) break; } catch {} await sleep(200); }
  let token = '';
  for (let i = 0; i < 50 && !token; i++) { try { token = readFileSync(path.join(data, 'd/owner.token'), 'utf8').trim(); } catch { await sleep(100); } }
  assert.ok(token, 'the owner token');
  // the page's `hass.fetchWithAuth`: the seat's address, the owner's token (the page itself sends its cookie)
  const fetchWithAuth = (p, o = {}) => fetch(`http://127.0.0.1:${port}${p}`, { ...o, headers: { ...(o.headers || {}), Authorization: `Bearer ${token}` } });

  const ctx = vm.createContext({ console, performance, setInterval, clearInterval, setTimeout, clearTimeout, Math, JSON, Map, Set, Float32Array, Uint8Array, Promise, Date,
    document: { hidden: false }, HTMLElement: class {}, import: undefined });
  ctx.window = ctx;
  let Panel = null;
  ctx.customElements = { get: () => undefined, define: (name, cls) => { Panel = cls; } };
  const load = (file) => vm.runInContext(readFileSync(path.join(repo, 'membrane/ui', file), 'utf8'), ctx, { filename: file });
  load('field3d.js');
  load('field.js');
  assert.ok(Panel && ctx.MembraneField3D, 'the page defines its two views');

  // ---- the 3D window: the page's own projection loader on the seat's answer
  const statuses = [];
  const view = new ctx.MembraneField3D(null, { get: async (p) => { const r = await fetchWithAuth('/ui/serve' + p); assert.equal(r.status, 200, p); return r.json(); },
    kindColor: () => '#888', onStatus: (t) => statuses.push(t), isAdmin: false });
  view.THREE = { Color: class {} };
  const plain = (v) => JSON.parse(JSON.stringify(v));       // the page's objects come from another realm (vm)
  const info = await view.load();
  assert.equal(info.n_total, 0, 'an empty projection');
  assert.equal(view.slots.size, 0);
  assert.deepEqual(plain(view.kinds), []);
  assert.match(statuses.at(-1), /^showing 0 of 0/);
  // Blurp's overlay, polled the way the page polls it, is an empty layer
  const ov = await view.api.get('/membrane/map/blurp');
  view.setBlurp(ov);
  assert.deepEqual(plain(view._blurp.lines), { shadow: [], proposal: [], suggest: [] });
  assert.deepEqual(plain(view._blurp.halos), []);

  // ---- the panel: _load (2D) and _load3d, with the page's own code, end on "the field is empty"
  const mk = (mode) => {
    const p = Object.create(Panel.prototype);
    const el = () => ({ style: {}, textContent: '', classList: { toggle() {}, add() {}, remove() {} } });
    Object.assign(p, { _msg: el(), _status3: el(), _host3d: el(), _m: 1200, _hidden: new Set(), _ax: { x: { kind: 'pc' }, y: { kind: 'pc' } }, _dists: {},
      _hass: { fetchWithAuth, user: {} }, getAttribute: (n) => (n === 'mode' ? mode : null), _paintAxes() {}, _paintLegend() {}, _relayout() {}, _hudOnce() {},
      _paint3dAxes() {}, _paintLegend3d() {}, _paintAgents() {} });
    return p;
  };
  const two = mk('2d');
  await two._load();
  assert.equal(two._msg.textContent, 'the field is empty', '2D');
  assert.equal(two._msg.style.display, '');
  assert.deepEqual(two._data.nodes, []);
  clearInterval(two._hudTimer);
  // the window: the page's 3D class, started with the real loader (WebGL is the one part replaced)
  ctx.window.MembraneField3D = class extends ctx.MembraneField3D { async start() { this.THREE = { Color: class {} }; await this.load(); } };
  const three = mk('3d');
  await three._load3d();
  assert.equal(three._msg.textContent, 'the field is empty', '3D');
  assert.equal(three._v3.info.n_total, 0);
  clearInterval(three._hudTimer);
  console.log('field empty: ok (the 2D panel and the 3D window end on "the field is empty" on the seat\'s own answers)');
} finally { seat.kill(); await sleep(300); rmSync(data, { recursive: true, force: true }); }
