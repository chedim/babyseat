// The door's MCP endpoint as the page uses it: JSON-RPC 2.0 over POST /mcp, initialize once, then tools/call.
// Plain static JavaScript, no build step. (A later piece ports the viewer to c1f view packs in the browser VM.)
let seq = 0;
let ready = null;

async function post(body) {
  const r = await fetch('/mcp', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  if (r.status === 202) return {};
  const j = await r.json();
  if (j.error) throw new Error(j.error.message);
  return j.result;
}

function init() {
  if (!ready) {
    ready = post({ jsonrpc: '2.0', id: ++seq, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'barest-page', version: '1' } } })
      .then(() => post({ jsonrpc: '2.0', method: 'notifications/initialized' }))
      .catch((e) => { ready = null; throw e; });
  }
  return ready;
}

export async function tool(name, args) {
  await init();
  const r = await post({ jsonrpc: '2.0', id: ++seq, method: 'tools/call', params: { name, arguments: args } });
  const text = (r.content && r.content[0] && r.content[0].text) || '';
  if (r.isError) throw new Error(text);
  return text;
}

// a row of entity_query: `<eid> (<Class>)`, with ` {props}` when full. An eid may hold spaces, so cut at the last ` (Class)` pair.
const ROW = /^(.*?) \(([A-Za-z0-9_]+)\)(?: (\{.*\}))?$/;
export function parseRows(text) {
  const rows = [];
  for (const line of text.split('\n')) {
    const m = ROW.exec(line);
    if (m) rows.push({ eid: m[1], cls: m[2], props: m[3] ? safeJSON(m[3]) : null });
  }
  return rows;
}

function safeJSON(s) { try { return JSON.parse(s); } catch { return null; } }

/** every entity matching q, paged 100 at a time up to `cap` rows -> {rows, more} */
export async function queryAll(q, cap = 600) {
  const rows = [];
  for (let offset = 0; offset < cap; offset += 100) {
    const page = parseRows(await tool('entity_query', { q, k: 100, offset }));
    rows.push(...page);
    if (page.length < 100) return { rows, more: false };
  }
  return { rows, more: true };
}

/** an entity's stored properties -> {cls, props} or null */
export async function readEntity(eid) {
  const t = await tool('entity_read', { spec: eid });
  if (!t.startsWith(eid + ' (')) return null;
  const m = /^\((.*?)\) ([\s\S]*)$/.exec(t.slice(eid.length + 1));
  return m ? { cls: m[1], props: safeJSON(m[2]) || {} } : null;
}
