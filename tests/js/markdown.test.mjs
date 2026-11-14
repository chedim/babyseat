// MARKDOWN AS c1f (membrane/apps/barest.c1f/markdown.c1f, #1143) on the wasm engine in Node. The cases are the old renderer's (v0.7.53 membrane/ui/panel.js `md`: fenced code with a language and a Copy
// button, headings, hr, quotes, bullet and numbered lists, tables, inline code / bold / italic / strikethrough / links) plus what the old one never had to survive: a message that attacks the page.
// The test plays the HOST: it keeps the DomNodes the pack wrote as a tree (tag, class, text, href, parent) and asserts STRUCTURE and COUNTS, never time. Nothing here is HTML: every node is an
// element with textContent, so `<script>` is a text node.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const PACK = readFileSync(fileURLToPath(new URL('../../membrane/apps/barest.c1f/markdown.c1f', import.meta.url)), 'utf8');

function world() {
  const f = new Field();
  f.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n");
  f.set_pack('markdown', PACK);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'markdown loads');
  const w = { f, t: 1000, nodes: new Map(), log: [], dom: 0 };
  w.flush = () => {
    const o = JSON.parse(f.drain());
    for (const x of o.writes) {
      w.log.push(x);
      if (!x.eid.startsWith('DomNode:')) continue;
      if (x.prop === 'retire') { w.nodes.delete(x.eid); continue; }
      const n = w.nodes.get(x.eid) || {}; if (!x.born) { n[x.prop] = x.new; w.dom++; } w.nodes.set(x.eid, n);
    }
  };
  w.tick = (dt) => { w.t += dt; f.tick(w.t); w.flush(); };
  w.write = (e, p, v) => { f.write(e, p, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.md = (id, text) => { if (f.get('Md:' + id) === 'null') f.load('Md:' + id, 'Md', '{}'); w.write('Md:' + id, 'into', 'DomNode:root'); w.write('Md:' + id, 'src', text); };
  // the children of a node in the order the page would hold them: made in order, a node with `before` sits above that sibling (fieldvm.js attach)
  w.kids = (eid) => { const l = [...w.nodes].filter(([, n]) => n.parent === eid).map(([k, n]) => ({ k, ...n })); for (const n of [...l]) if (n.before) { const j = l.findIndex((x) => x.k === n.before); if (j >= 0) { l.splice(l.indexOf(n), 1); l.splice(l.findIndex((x) => x.k === n.before), 0, n); } } return l; };
  // the tree as a compact text: <tag.class href=..>text children</tag>
  w.html = (eid = 'DomNode:root') => w.kids(eid).map((n) => `<${n.tag || 'div'}${n.href ? ` href="${n.href}"` : ''}${n['data-lang'] ? ` lang="${n['data-lang']}"` : ''}>${n.text ?? ''}${w.html(n.k)}</${n.tag || 'div'}>`).join('');
  w.text = (eid = 'DomNode:root') => w.kids(eid).map((n) => (n.text ?? '') + w.text(n.k)).join('');
  w.all = () => [...w.nodes].map(([k, n]) => ({ k, ...n }));
  w.failed = () => w.log.filter((x) => x.prop === 'failed' || x.prop === 'refused');
  return w;
}
const render = (text) => { const w = world(); w.md('m', text); assert.deepEqual(w.failed(), [], 'no evaluation failed'); return w; };
const strip = (h) => h.replace(/<span>|<\/span>/g, '');            // the inline spans carry no tag semantics of their own in these cases
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

scenario('paragraphs and line breaks', () => {
  const w = render('one\ntwo\n\nthree');
  const ps = w.all().filter((x) => x.tag === 'p'); assert.equal(ps.length, 2);
  assert.equal(w.all().filter((x) => x.class === 'md-ln').length, 3, 'a line is a line');
  assert.equal(w.text(), 'onetwothree');
});
scenario('headings: # is h3 ... ###### is h6 (the old page: level + 2)', () => {
  const w = render('# a\n\n## b\n\n### c\n\n###### f');
  assert.deepEqual(w.all().filter((x) => /^h\d$/.test(x.tag)).map((x) => x.tag), ['h3', 'h4', 'h5', 'h6']);
  assert.equal(render('#nospace').all().filter((x) => /^h\d$/.test(x.tag)).length, 0, '#word is text');
});
scenario('hr, blockquote (nested), and a quote holds markdown', () => {
  const w = render('---\n\n> one\n> > two\n\n***');
  assert.equal(w.all().filter((x) => x.tag === 'hr').length, 2);
  const qs = w.all().filter((x) => x.tag === 'blockquote'); assert.equal(qs.length, 2, 'a quote in a quote');
  assert.equal(w.text(), 'onetwo');
  assert.equal(render('> - a\n> - b').all().filter((x) => x.tag === 'li').length, 2, 'a list in a quote');
});
scenario('lists: bullets and numbers, nested three levels, mixed kinds are separate lists', () => {
  const w = render('- a\n- b\n  - c\n    - d\n  - e\n- f\n\n1. x\n2. y');
  const uls = w.all().filter((x) => x.tag === 'ul'), ols = w.all().filter((x) => x.tag === 'ol');
  assert.equal(uls.length, 3, 'the list and its two nested lists'); assert.equal(ols.length, 1);
  assert.equal(w.all().filter((x) => x.tag === 'li').length, 8);
  const b = w.all().find((x) => x.tag === 'li' && w.text(x.k) === 'bcde'); assert.ok(b, 'item b holds its sub-list: c, d (inside c), e');
  assert.equal(render('1) one\n2) two').all().filter((x) => x.tag === 'ol').length, 1, '1) is a number too');
  assert.equal(render('* s\n+ p').all().filter((x) => x.tag === 'li').length, 2);
});
scenario('tables: header row, body rows, inline markup in a cell; no separator row is a paragraph', () => {
  const w = render('| a | b |\n|---|:-:|\n| **x** | `y` |\n| 3 | 4 |');
  assert.equal(w.all().filter((x) => x.tag === 'th').length, 2); assert.equal(w.all().filter((x) => x.tag === 'td').length, 4);
  assert.equal(w.all().filter((x) => x.tag === 'tr').length, 3);
  assert.deepEqual(w.all().filter((x) => x.tag === 'th').map((x) => w.text(x.k)), ['a', 'b']);
  assert.ok(w.all().some((x) => x.class === 'md-t md-b' && x.text === 'x'), 'bold in a cell');
  const p = render('| a | b |\n| c | d |'); assert.equal(p.all().filter((x) => x.tag === 'table').length, 0); assert.equal(p.all().filter((x) => x.tag === 'p').length, 1);
});
scenario('fenced code: language label, a Copy button, the body whole; ~~~ too; an unterminated fence is code to the end', () => {
  const w = render('before\n```js\nlet a = 1;\n  let b;\n```\nafter');
  const pre = w.all().find((x) => x.tag === 'pre'); assert.equal(pre['data-lang'], 'js');
  const copy = w.all().find((x) => x.class === 'md-copy'); assert.equal(copy.text, 'Copy'); assert.equal(copy.act, 'md-copy');
  assert.equal(w.all().find((x) => x.class === 'md-lang').text, 'js');
  assert.equal(w.all().find((x) => x.class === 'md-codebody').text, 'let a = 1;\n  let b;');
  assert.equal(w.all().filter((x) => x.tag === 'p').length, 2, 'text before and after');
  assert.equal(render('~~~py\nx\n~~~').all().find((x) => x.class === 'md-codebody').text, 'x');
  assert.equal(render('```\nno lang').all().find((x) => x.class === 'md-lang').text, 'code');
  assert.equal(render('a\n```js\nunterminated\nmore').all().find((x) => x.class === 'md-codebody').text, 'unterminated\nmore');
  assert.equal(render('- item\n   ```sh\n   ls\n   ```').all().filter((x) => x.tag === 'pre').length, 1, 'an indented fence');
  assert.equal(render('```\n```').all().find((x) => x.class === 'md-codebody').text, '', 'an empty block');
});
scenario('the Copy click writes the code to the host: {text, at}, once per click', () => {
  const w = render('```js\nlet a = 1;\n```');
  const copy = w.all().find((x) => x.class === 'md-copy').k;
  w.t = 2000; w.write(copy, 'clicked', 2000);
  assert.deepEqual(JSON.parse(w.f.get(copy)).copy, { text: 'let a = 1;', at: 2000 });
  w.write(copy, 'clicked', 2001);
  assert.equal(JSON.parse(w.f.get(copy)).copy.at, 2001, 'a second click is a new write');
});
scenario('inline: code, bold, italic, strikethrough, nested, links, bare urls', () => {
  const w = render('a `co*de*` **b *i* b** ~~s~~ [l](https://x.io/p?q=1) see https://y.io/z. ok');
  const by = (cls) => w.all().filter((x) => x.class?.split(' ').includes(cls)).map((x) => x.text);
  assert.deepEqual(by('md-code'), ['co*de*']); assert.deepEqual(by('md-s'), ['s']);
  assert.deepEqual(by('md-b'), ['b ', 'i', ' b']); assert.deepEqual(by('md-i'), ['i']);
  assert.deepEqual(w.all().filter((x) => x.tag === 'a').map((x) => [x.text, x.href]), [['l', 'https://x.io/p?q=1'], ['https://y.io/z', 'https://y.io/z']]);
  assert.equal(w.text().endsWith('. ok'), true, 'the full stop after a url stays text');
  assert.equal(render('5 * 3 and **open').text(), '5 * 3 and **open', 'a toggle with no partner is the literal characters');
});
scenario('everything a message can attack with is inert text: no script, no img, no javascript: link', () => {
  const evil = '<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[x](javascript:alert(1)) [y](data:text/html,hi) [z](//evil.io) <a href="javascript:1">q</a>';
  const w = render(evil);
  assert.equal(w.all().filter((x) => ['script', 'img', 'iframe'].includes(x.tag)).length, 0);
  assert.equal(w.all().filter((x) => x.tag === 'a').length, 0, 'no link at all: none is http(s)');
  assert.equal(w.all().filter((x) => x.href != null).length, 0);
  assert.ok(w.text().includes('<script>alert(1)</script>') && w.text().includes('<img src=x onerror=alert(1)>'), 'the markup is shown as text');
  assert.ok(w.all().every((x) => !x.tag || /^(div|span|p|h[3-6]|ul|ol|li|a|code|pre|button|blockquote|hr|table|thead|tbody|tr|th|td)$/.test(x.tag)), 'only allow-listed tags are ever asked for');
});
scenario('hostile sizes finish without a failed evaluation: a long line, 10,000 items, deep quotes, 3000 fences, deep lists, 5000 markers, a huge table', () => {
  const big = {
    line: 'x'.repeat(100000),
    items: Array.from({ length: 10000 }, (_, i) => `- item ${i}`).join('\n'),
    quotes: '> '.repeat(500) + 'deep',
    fences: Array.from({ length: 3000 }, () => '```\nx\n```').join('\n'),
    nest: Array.from({ length: 60 }, (_, i) => ' '.repeat(i * 2) + '- n' + i).join('\n'),
    stars: '*'.repeat(5000) + '`'.repeat(5000) + '['.repeat(5000) + '~'.repeat(5000),
    table: '| ' + Array.from({ length: 40 }, (_, i) => 'c' + i).join(' | ') + ' |\n|' + '---|'.repeat(40) + '\n' + Array.from({ length: 200 }, () => '| ' + Array.from({ length: 40 }, (_, i) => 'v' + i).join(' | ') + ' |').join('\n'),
    tokens: Array.from({ length: 40 }, () => 'x ' + '*a* '.repeat(11)).join('\n'),
    urls: Array.from({ length: 500 }, (_, i) => 'http://a.com/' + i).join(' '),
  };
  for (const [k, v] of Object.entries(big)) { const w = render(v); assert.ok(w.all().length > 0, k); }
});
scenario('over the cap the rest is a plain preview with a Show all button that parses it', () => {
  const text = 'a\n\n' + Array.from({ length: 3000 }, (_, i) => `line ${i}`).join('\n\n');
  const w = render(text);
  const btn0 = w.all().find((x) => x.class === 'md-showall'); const btn = btn0 && [btn0.k, btn0];
  assert.ok(btn, 'a Show all button'); assert.match(btn[1].text, /^Show all \(\d+ more characters\)$/);
  assert.ok(w.all().find((x) => x.class === 'md-plain').text.endsWith('…'));
  const before = w.all().filter((x) => x.tag === 'p').length;
  w.write(btn[0], 'clicked', 3000);
  assert.ok(w.all().filter((x) => x.tag === 'p').length > before, 'all of it is parsed now');
  assert.equal(w.all().filter((x) => x.class === 'md-showall').length, 0, 'the button is gone');
  assert.deepEqual(w.failed(), []);
});
scenario('streaming: unchanged text is ZERO writes; a growing last line is one text write; earlier blocks are not touched', () => {
  const w = world();
  const full = ['# Title', '', 'Intro **bold**.', '', ...Array.from({ length: 12 }, (_, i) => `- item ${i}`), '', '```py', 'print(1)', '```', '', 'tail words'].join('\n');
  w.md('s', full);
  const n0 = w.log.length; w.write('Md:s', 'src', full);
  assert.equal(w.log.length, n0, 'the same text again: no write at all');
  const grow = (text) => { const d0 = w.dom, l0 = w.log.length; w.write('Md:s', 'src', text); return { dom: w.dom - d0, log: w.log.slice(l0) }; };
  const a = grow(full + ' and'); const b = grow(full + ' and more');
  assert.ok(a.dom <= 3 && b.dom <= 3, `a growing last line changes at most its own nodes (${a.dom}, ${b.dom})`);
  assert.equal(b.log.filter((x) => x.eid.startsWith('DomNode:') && x.prop === 'text').length, 1, 'exactly one text write');
  assert.ok(b.log.filter((x) => x.eid.startsWith('DomNode:')).every((x) => /^DomNode:md:s:c2:/.test(x.eid) || /^DomNode:md:s:c3:/.test(x.eid)), 'only the last chunk is redrawn');
  assert.deepEqual(w.failed(), []);
});
scenario('ORDER: the blocks of a long mixed message come out in the order of the text (found by the screenshot: entities draw in whatever order the engine runs them)', () => {
  const doc = ['# h', 'para', '- a', '- b', '  - c', '1. x', '| t | u |', '|---|---|', '| 1 | 2 |', '> q', '---', '```js', 'code', '```', 'tail', '#### h2', 'last para'].join('\n');
  const w = render(doc);
  const order = []; const walk = (eid) => { for (const n of w.kids(eid)) { if (/^(h\d|p|ul|ol|table|blockquote|hr|pre)$/.test(n.tag)) order.push(n.tag); if (n.tag !== 'ul' && n.tag !== 'ol') walk(n.k); else { order.push('<'); walk(n.k); order.push('>'); } } };
  walk('DomNode:root');
  assert.deepEqual(order.filter((t) => t !== '<' && t !== '>').filter((t, i, a) => !(i && (t === 'ul') && a[i - 1] === 'ul')), ['h3', 'p', 'ul', 'ol', 'table', 'blockquote', 'p', 'hr', 'pre', 'p', 'h6', 'p']);
  // and past one chunk (40 lines): the second chunk follows the first
  const long = Array.from({ length: 90 }, (_, i) => `para ${i}\n`).join('\n');
  const texts = []; const w2 = render(long); const collect = (eid) => { for (const n of w2.kids(eid)) { if (n.tag === 'span' && /^para \d+$/.test(n.text ?? '')) texts.push(Number(n.text.slice(5))); collect(n.k); } }; collect('DomNode:root');
  assert.deepEqual(texts, [...texts].sort((a, b) => a - b)); assert.ok(texts.length >= 80);
});

scenario('Copy: "Copied" is announced and goes back to "Copy" after 2 s of the clock; a refusal is visible text', () => {
  const w = render('```js\nlet a = 1;\n```');
  const copy = w.all().find((x) => x.class === 'md-copy'), live = w.all().find((x) => x.class === 'md-live');
  assert.equal(copy['aria-label'], 'Copy code'); assert.equal(live.role, 'status'); assert.equal(live['aria-live'], 'polite'); assert.equal(live.text ?? '', '');
  w.write(copy.k, 'clicked', w.t);
  assert.equal(JSON.parse(w.f.get(copy.k)).copy.text, 'let a = 1;', 'the click asks the host to copy');
  w.write(copy.k, 'copy_result', { ok: true, at: w.t });                     // the host's answer
  assert.equal(w.nodes.get(copy.k).text, 'Copied'); assert.equal(w.nodes.get(live.k).text, 'Copied', 'announced');
  w.tick(1.0); assert.equal(w.nodes.get(copy.k).text, 'Copied', 'still there after 1 s');
  w.tick(1.2); assert.equal(w.nodes.get(copy.k).text, 'Copy', 'back after 2 s'); assert.equal(w.nodes.get(live.k).text, '', 'the announcement is cleared');
  w.write(copy.k, 'clicked', w.t);
  w.write(copy.k, 'copy_result', { ok: false, why: 'the browser refused the clipboard', at: w.t });
  assert.equal(w.nodes.get(copy.k).text, 'Copy failed'); assert.equal(w.nodes.get(live.k).text, 'Copy failed: the browser refused the clipboard');
  w.tick(2.5); assert.equal(w.nodes.get(copy.k).text, 'Copy');
});
scenario('keyboard and small screens: a code block and a table scroll as focusable regions with a label; Show all is a button', () => {
  const w = render('```python\nx\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |');
  const body = w.all().find((x) => x.class === 'md-codebody'); assert.equal(body.tabindex, 0); assert.equal(body.role, 'region'); assert.equal(body['aria-label'], 'python code');
  assert.equal(w.all().find((x) => x.class === 'md-codebody').tag, 'code');
  const wrap = w.all().find((x) => x.class === 'md-tablewrap'); assert.equal(wrap.tabindex, 0); assert.equal(wrap.role, 'region'); assert.equal(wrap['aria-label'], 'table');
  const big = render('a\n\n' + Array.from({ length: 3000 }, (_, i) => `line ${i}`).join('\n\n'));
  const btn = big.all().find((x) => x.class === 'md-showall'); assert.equal(btn.tag, 'button'); assert.equal(btn.type, 'button');
  assert.equal(render('```\nx\n```').all().find((x) => x.class === 'md-codebody')['aria-label'], 'code', 'no language: just code');
});
scenario('a text that SHRINKS retires the nodes of the blocks that went (grow, shrink: the same nodes as a fresh render)', () => {
  const small = 'first paragraph\n\n- a\n- b';
  const big = small + '\n\n> a quote\n\n| t | u |\n|---|---|\n| 1 | 2 |\n\n```js\ncode\n```\n\n1. x\n   - y\n\ntail';
  const fresh = render(small); const w = world();
  w.md('s', small); const n0 = w.nodes.size, h0 = w.html();
  w.write('Md:s', 'src', big); assert.ok(w.nodes.size > n0 + 20, 'it grew');
  w.write('Md:s', 'src', small);
  assert.equal(w.html(), h0, 'the page shows the small text again'); assert.equal(w.nodes.size, fresh.nodes.size, 'and holds no node of the blocks that went');
  assert.deepEqual(w.failed(), []);
  // a list losing items, a long text losing a whole chunk, a code block turning into prose
  const l = world(); l.md('l', '- a\n- b\n- c\n  - d'); const nl = render('- a').nodes.size; l.write('Md:l', 'src', '- a'); assert.equal(l.nodes.size, nl);
  const long = Array.from({ length: 120 }, (_, i) => `para ${i}`).join('\n\n'); const c = world(); c.md('c', long); assert.ok(c.nodes.size > 400); c.write('Md:c', 'src', 'short'); assert.equal(c.nodes.size, render('short').nodes.size, 'chunks past the first are gone');
  const k = world(); k.md('k', '```js\nx\n```'); k.write('Md:k', 'src', 'plain words'); assert.equal(k.nodes.size, render('plain words').nodes.size);
});
console.log(`markdown: ok (${n} scenarios)`);
