// WCAG AA (4.5:1) for the text styles of the chat's new elements, in the light and the dark theme, computed from the colour variables of app.css itself (so a changed colour is checked, not a copy of it):
// headings and paragraphs (ink on the bubble), quotes and h5/h6 and the code bar (mute), links, inline code (ink over the grey wash), the table header, the tool rows, the Stop button, the note.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const css = readFileSync(fileURLToPath(new URL('../../membrane/apps/barest.c1f/ui/app.css', import.meta.url)), 'utf8');
const vars = (block) => Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
const light = vars(css.match(/:root\s*\{([^}]*)\}/)[1]);
const dark = { ...light, ...vars(css.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/)[1]) };
assert.deepEqual(vars(css.match(/:root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}/)[1]), vars(css.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/)[1]), 'the system dark theme and the chosen dark theme are the same colours');
const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const lum = (h) => { const [r, g, b] = rgb(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const wash = (bg, a) => '#' + rgb(bg).map((c) => Math.round(a * 127 + (1 - a) * c).toString(16).padStart(2, '0')).join('');
assert.ok(css.includes('rgb(127 127 127 / .18)') && css.includes('rgb(127 127 127 / .12)'), 'the washes this test assumes');
let n = 0;
for (const [theme, t] of [['light', light], ['dark', dark]]) {
  const pairs = [['ink', 'seat'], ['ink', 'me'], ['ink', 'panel'], ['mute', 'seat'], ['mute', 'panel'], ['link', 'seat'], ['link', 'me'], ['link', 'panel'], ['stop-ink', 'stop'], ['note-ink', 'note'], ['mute', 'bg']];
  for (const [f, b] of pairs) { assert.ok(t[f] && t[b], `${theme}: --${f} / --${b} exist`); assert.ok(ratio(t[f], t[b]) >= 4.5, `${theme}: ${f} on ${b} is ${ratio(t[f], t[b]).toFixed(2)}`); n++; }
  assert.ok(ratio(t.ink, wash(t.seat, 0.18)) >= 4.5, `${theme}: inline code`); assert.ok(ratio(t.ink, wash(t.seat, 0.12)) >= 4.5, `${theme}: table header`);
  assert.ok(ratio(t.accent, t.panel) >= 3 && ratio(t.accent, t.seat) >= 3, `${theme}: the focus ring is 3:1 against both surfaces`); n += 3;
}
console.log(`chat contrast: ok (${n} pairs, AA 4.5:1)`);
