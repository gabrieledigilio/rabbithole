/* Rabbit Hole: the page. No build step, no framework: D3 for the layout maths, the rest by hand.
   Sections: helpers · SQL drawer · search box · rings (the picture) · timeline · explore · pages · router */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const view = $('#view');

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(3)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const svgEl = (tag, attrs) => { const el = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) el.setAttribute(k, attrs[k]); return el; };

const NUM = new Intl.NumberFormat('en-GB');
const n = (x) => NUM.format(x);
const MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const toMs = (v) => (typeof v === 'number' ? v : Date.parse(String(v).slice(0, 10) + 'T00:00:00Z'));
const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
const longDay = (v) => { const d = new Date(toMs(v)); return `${d.getUTCDate()} ${MONTH[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const monthYear = (ms) => { const d = new Date(ms); return `${MONTH[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const yearOf = (v) => new Date(toMs(v)).getUTCFullYear();
const DAY = 86400000, YEAR = 365.25 * DAY;
function howLong(ms) {
  if (ms < DAY) { const hrs = Math.round(ms / 3600000); return hrs < 1 ? 'less than an hour' : `${hrs} hour${hrs === 1 ? '' : 's'}`; }
  if (ms < 60 * DAY) { const d = Math.round(ms / DAY); return `${d} day${d === 1 ? '' : 's'}`; }
  if (ms < 2 * YEAR) return `${Math.round(ms / (30.44 * DAY))} months`;
  return `${(ms / YEAR).toFixed(1).replace('.0', '')} years`;
}
const plural = (count, one, many) => `${n(count)} ${count === 1 ? one : (many || one + 's')}`;
const wiki = (lang, title) => `https://${lang}.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, '_'))}`;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// The link's words sit between ⟦ and ⟧ in a context sentence; '⌂ ' in front = inside a table or template.
function contextNodes(text) {
  if (!text) return [h('span', { class: 'faint' }, 'The sentence was not recorded.')];
  const boxed = text.startsWith('⌂ ');
  const m = (boxed ? text.slice(2) : text).match(/^([\s\S]*?)⟦([\s\S]*?)⟧([\s\S]*)$/);
  const body = m ? [m[1], h('mark', null, m[2]), m[3]] : [text];
  return boxed ? [...body, h('span', { class: 'faint' }, ' (in a table, template or reference)')] : body;
}

// ------------------------------------------------------------------ SQL drawer
const Sql = {
  calls: [],
  add(label, statements) {
    if (!statements || !statements.length) return;
    this.calls.unshift({ label, statements });
    this.calls.length = Math.min(this.calls.length, 14);
    this.paint();
  },
  reset() { this.calls = []; this.paint(); },
  colour(sql) {
    const out = [];
    const token = /('(?:[^']|'')*')|(<vector of \d+ floats>)|(FOR SYSTEM_TIME(?: AS OF(?: TIMESTAMP)?| ALL)?|system_versioning_asof|VEC_DISTANCE_COSINE|MATCH|AGAINST)|\b(SELECT|FROM|WHERE|LEFT JOIN|JOIN|ON|AND|OR|GROUP BY|ORDER BY|LIMIT|WITH|AS|UNION ALL|SET|EXISTS|NOT|NULL|IS|IN|DESC|HAVING|OVER|DISTINCT|COUNT|SUM|MAX|MIN|IF|ROUND|ROW_NUMBER|EXTRACT|YEAR_MONTH|LEAST|DEFAULT)\b/g;
    let last = 0, m;
    const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    while ((m = token.exec(sql))) {
      out.push(esc(sql.slice(last, m.index)));
      const cls = m[1] || m[2] ? 's' : m[3] ? 't' : 'k';
      out.push(`<span class="${cls}">${esc(m[0])}</span>`);
      last = token.lastIndex;
    }
    out.push(esc(sql.slice(last)));
    return out.join('');
  },
  paint() {
    const all = this.calls.flatMap((c) => c.statements);
    const total = all.reduce((a, s) => a + s.ms, 0);
    const btn = $('#sql-toggle');
    btn.textContent = btn.getAttribute('aria-expanded') === 'true' ? 'Hide the SQL' : 'Show the SQL';
    if (all.length) btn.append(h('span', { class: 'n num' }, `${all.length} statements, ${total < 10 ? total.toFixed(1) : Math.round(total)} ms`));
    if ($('#sql-drawer').hidden) return;
    $('#sql-summary').textContent = all.length
      ? `${all.length} statements for what is on screen, ${Math.round(total)} ms in MariaDB. Time travel and vector search are highlighted.`
      : 'Nothing has been asked yet.';
    $('#sql-list').replaceChildren(...this.calls.map((c) => h('div', { class: 'sql-call' },
      h('h3', null, c.label),
      c.statements.map((s) => {
        const pre = h('pre');
        pre.innerHTML = this.colour(s.sql);
        return h('div', { class: 'sql-st' },
          h('span', { class: 'cost' }, `${s.ms} ms, ${plural(s.rows, 'row')}`),
          s.note ? h('div', { class: 'note' }, s.note) : null, pre);
      }))));
  },
  toggle(open) {
    const drawer = $('#sql-drawer');
    drawer.hidden = open === undefined ? !drawer.hidden : !open;
    $('#sql-toggle').setAttribute('aria-expanded', String(!drawer.hidden));
    this.paint();
  },
};
$('#sql-toggle').addEventListener('click', () => Sql.toggle());
$('#sql-close').addEventListener('click', () => Sql.toggle(false));

async function api(path, label) {
  const r = await fetch(path);
  const data = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(data.error || r.statusText);
  if (label && data.sql) Sql.add(label, data.sql);
  return data;
}

// Read a streamed answer into an element, turning [1] into links when sources are given.
async function say(el, path, body, sources) {
  el.className = el.className.replace(' warn', '');
  el.textContent = '…';
  let text = '';
  const paint = () => {
    el.classList.toggle('warn', text.startsWith('⚠'));
    if (!sources) { el.textContent = text; return; }
    el.replaceChildren(...text.split(/(\[\d+\])/).map((part) => {
      const m = part.match(/^\[(\d+)\]$/);
      const src = m && sources[Number(m[1]) - 1];
      return src ? h('sup', null, h('a', { href: `#/a/${src.page_id}`, title: src.title }, m[1])) : part;
    }));
  };
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const reader = r.body.getReader(), dec = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      text += dec.decode(value, { stream: true });
      paint();
    }
    if (!text) { text = '⚠ The language model returned nothing.'; paint(); }
  } catch (e) { text = `⚠ ${e.message}`; paint(); }
}

// ------------------------------------------------------------------ tooltip
const Tip = {
  el: $('#tip'),
  show(x, y, kids) {
    this.el.replaceChildren(...kids);
    this.el.hidden = false;
    const r = this.el.getBoundingClientRect();
    this.el.style.left = Math.max(8, Math.min(x + 14, innerWidth - r.width - 8)) + 'px';
    this.el.style.top = Math.max(8, Math.min(y + 16, innerHeight - r.height - 8)) + 'px';
  },
  hide() { this.el.hidden = true; },
};

// ------------------------------------------------------------------ search box: titles as you type, a question on Enter
function suggestBox(input, list, onPick, askRow) {
  let items = [], at = -1, seq = 0;
  const close = () => { list.hidden = true; at = -1; };
  const paint = () => {
    const text = input.value.trim();
    const rows = items.map((it, i) => h('li', { role: 'option', 'aria-selected': String(i === at), onmousedown: (e) => { e.preventDefault(); pick(i); } },
      h('span', { class: 't' }, it.title), it.title_en && it.title_en !== it.title ? h('span', { class: 'faint' }, it.title_en) : null));
    if (askRow && text) rows.push(h('li', { class: 'ask-row', role: 'option', 'aria-selected': String(at === items.length), onmousedown: (e) => { e.preventDefault(); pick(items.length); } },
      h('span', null, 'Ask Wikipedia: '), h('span', { class: 't' }, text)));
    list.replaceChildren(...rows);
    list.hidden = !rows.length;
  };
  const pick = (i) => {
    const text = input.value.trim();
    close();
    if (i >= 0 && i < items.length) { onPick(items[i]); input.value = askRow ? '' : items[i].title; } else if (askRow && text) askRow(text);
    input.blur();
  };
  const load = debounce(async () => {
    const text = input.value.trim(), mine = ++seq;
    if (text.length < 2) { items = []; paint(); return; }
    const rows = await api('/api/suggest?q=' + encodeURIComponent(text)).catch(() => []);
    if (mine === seq) { items = rows; at = -1; paint(); }
  }, 120);
  input.addEventListener('input', load);
  input.addEventListener('focus', () => { if (input.value.trim().length >= 2) load(); });
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (e) => {
    const count = items.length + (askRow && input.value.trim() ? 1 : 0);
    if (e.key === 'ArrowDown') { at = Math.min(at + 1, count - 1); paint(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { at = Math.max(at - 1, -1); paint(); e.preventDefault(); }
    else if (e.key === 'Escape') close();
    else if (e.key === 'Enter') {
      e.preventDefault();
      // Enter on plain text: a question if it looks like one, otherwise the best title.
      const text = input.value.trim();
      if (at >= 0) pick(at);
      else if (askRow && (/\s/.test(text) || !items.length)) pick(items.length);
      else if (items.length) pick(0);
    }
  });
  return { close };
}
suggestBox($('#q'), $('#suggest'), (it) => { location.hash = `#/a/${it.page_id}`; }, (text) => { location.hash = '#/ask?q=' + encodeURIComponent(text); });
$('#find').addEventListener('submit', (e) => e.preventDefault());

// ------------------------------------------------------------------ rings: the article's connections as growth rings
const INK = '#1c2430', INK2 = '#4f5b6b', INK3 = '#7b8694', RULE = '#dde2e8', LINK = '#3366cc', RED = '#c8102e', AMBER = '#c79100', OUTSIDE = '#8fa6d8';
const aliveAt = (periods, t) => !!periods && periods.some((p) => p[0] <= t && (p[1] == null || p[1] > t));

class Rings {
  constructor(canvas, on) {
    this.canvas = canvas; this.ctx = canvas.getContext('2d'); this.on = on;
    this.nodes = []; this.cross = []; this.t = 0; this.hover = null; this.selected = null; this.names = 'en';
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    canvas.addEventListener('mousemove', (e) => this.move(e));
    canvas.addEventListener('mouseleave', () => { this.hover = null; Tip.hide(); canvas.classList.remove('pick'); this.draw(); });
    canvas.addEventListener('click', (e) => { const nd = this.at(e); if (nd) this.on.pick(nd); });
    canvas.addEventListener('dblclick', (e) => { const nd = this.at(e); if (nd && !nd.outside) this.on.open(nd); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => this.draw());
  }

  setGraph(g) {
    this.center = g.center; this.end = g.end;
    const first = (nd) => Math.min(...[...(nd.out || []), ...(nd.in || [])].map((p) => p[0]));
    this.nodes = g.nodes.map((nd) => ({ ...nd, first: first(nd) })).sort((a, b) => a.first - b.first);
    this.t0 = Math.min(g.center.created, ...this.nodes.map((nd) => nd.first));
    const byId = new Map(this.nodes.map((nd) => [nd.id, nd]));
    this.cross = g.cross.filter((c) => byId.has(c.s) && byId.has(c.d)).map((c) => ({ a: byId.get(c.s), b: byId.get(c.d), p: c.p }));
    for (const nd of this.nodes) {
      nd.f = this.frac(nd.first);
      nd.size = nd.outside ? 2.6 : Math.max(3.4, Math.min(9.5, 3.2 + Math.sqrt(nd.weight || 0) * 0.3));
      nd.contested = (nd.out || []).length >= 3;
    }
    this.layout();
    this.hover = this.selected = null;
    this.draw();
  }

  frac(t) { return Math.max(0, Math.min(1, (t - this.t0) / (this.end - this.t0))); }

  // Radius is fixed by the date of the first link; the angle is free. A force layout in a virtual
  // disc pulls linked neighbours to the same side, then every node is snapped back onto its ring.
  layout() {
    const R0 = 110, R1 = 500;
    this.nodes.forEach((nd, i) => {
      nd.rv = R0 + Math.sqrt(nd.f) * (R1 - R0);
      const a = i * 2.399963;                       // the golden angle: an even spread to start from
      nd.x = Math.cos(a) * nd.rv; nd.y = Math.sin(a) * nd.rv;
    });
    const links = this.cross.map((c) => ({ source: c.a, target: c.b }));
    const sim = d3.forceSimulation(this.nodes)
      .force('ring', d3.forceRadial((d) => d.rv).strength(1))
      .force('apart', d3.forceManyBody().strength(-34).distanceMax(170))
      .force('near', d3.forceLink(links).strength(0.045).distance(80))
      .force('touch', d3.forceCollide((d) => d.size * 1.7 + 7).iterations(2))
      .stop();
    for (let i = 0; i < 240; i++) sim.tick();
    for (const nd of this.nodes) nd.theta = Math.atan2(nd.y, nd.x);
    for (let pass = 0; pass < 30; pass++) {       // on the ring itself, nudge apart what still overlaps
      for (let i = 0; i < this.nodes.length; i++) {
        for (let j = i + 1; j < this.nodes.length; j++) {
          const a = this.nodes[i], b = this.nodes[j];
          if (Math.abs(a.rv - b.rv) > 26) continue;
          let d = b.theta - a.theta;
          d = Math.atan2(Math.sin(d), Math.cos(d));
          const need = (a.size + b.size + 9) * 1.55 / ((a.rv + b.rv) / 2);
          if (Math.abs(d) < need) { const push = (need - Math.abs(d)) / 2 * (d < 0 ? -1 : 1); a.theta -= push; b.theta += push; }
        }
      }
    }
  }

  resize() {
    const box = this.canvas.parentElement.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    this.w = box.width; this.h = box.height;
    this.canvas.width = Math.round(this.w * dpr); this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  geometry() {
    const cx = this.w / 2, cy = this.h / 2 + 8;
    const rOut = Math.max(120, Math.min(this.w / 2 - (this.w < 560 ? 30 : 70), this.h / 2 - 34));   // the side room is for labels
    const rIn = Math.max(46, rOut * 0.17);
    return { cx, cy, rIn, rOut, r: (f) => rIn + Math.sqrt(f) * (rOut - rIn) };      // equal area per year: early years get room
  }

  place(nd, g) { const r = g.r(nd.f); nd.px = g.cx + Math.cos(nd.theta) * r; nd.py = g.cy + Math.sin(nd.theta) * r; }

  state(nd, t) {
    const out = aliveAt(nd.out, t), inn = aliveAt(nd.in, t);
    if (!out && !inn) return nd.first > t ? null : { gone: true };
    return { out, inn, red: !nd.outside && nd.created != null && nd.created > t };
  }

  setTime(t) { this.t = t; this.draw(); }
  label(nd) { return (this.names === 'en' && nd.en) || nd.title; }

  draw() {
    if (!this.w || !this.center) return;
    const c = this.ctx, g = this.geometry(), t = this.t;
    c.clearRect(0, 0, this.w, this.h);
    // rings: one per year, a label every fourth
    const y0 = new Date(this.t0).getUTCFullYear() + 1, y1 = new Date(this.end).getUTCFullYear();
    const every = (g.rOut - g.rIn) / Math.max(1, y1 - y0) < 13 ? 4 : 2;
    c.lineWidth = 1;
    for (let y = y0; y <= y1; y++) {
      const r = g.r(this.frac(Date.UTC(y, 0, 1)));
      c.strokeStyle = y % every === 0 ? '#ccd3dc' : '#e9edf1';
      c.beginPath(); c.arc(g.cx, g.cy, r, 0, 2 * Math.PI); c.stroke();
    }
    // what lies beyond the chosen day has not happened yet: veil it
    const rT = g.r(this.frac(t));
    c.save();
    c.beginPath(); c.rect(0, 0, this.w, this.h); c.arc(g.cx, g.cy, rT, 0, 2 * Math.PI, true);
    c.fillStyle = 'rgba(255,255,255,0.74)'; c.fill('evenodd');
    c.restore();
    c.font = '11px "IBM Plex Sans", sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    this.yearBoxes = [];
    for (let y = y0; y <= y1; y++) {
      if (y % every) continue;
      const r = g.r(this.frac(Date.UTC(y, 0, 1))), ax = g.cx, ay = g.cy - r;
      c.fillStyle = '#fff'; c.fillRect(ax - 15, ay - 7, 30, 14);
      c.fillStyle = Date.UTC(y, 0, 1) <= t ? INK3 : '#b9c1cb'; c.fillText(String(y), ax, ay);
      this.yearBoxes.push({ x0: ax - 17, y0: ay - 9, x1: ax + 17, y1: ay + 9 });
    }
    // the day itself
    c.strokeStyle = INK; c.lineWidth = 1.4; c.beginPath(); c.arc(g.cx, g.cy, rT, 0, 2 * Math.PI); c.stroke();

    const shown = [];
    for (const nd of this.nodes) { this.place(nd, g); nd.st = this.state(nd, t); if (nd.st) shown.push(nd); }
    const focus = this.hover || this.selected;
    // links among the neighbours: faint, so the clusters read without a hairball
    c.lineWidth = 1;
    for (const e of this.cross) {
      if (!e.a.st || !e.b.st || e.a.st.gone || e.b.st.gone || !aliveAt(e.p, t)) continue;
      const lit = focus && (e.a === focus || e.b === focus);
      c.strokeStyle = lit ? 'rgba(51,102,204,0.55)' : 'rgba(28,36,48,0.055)';
      c.beginPath(); c.moveTo(e.a.px, e.a.py);
      c.quadraticCurveTo(g.cx + ((e.a.px + e.b.px) / 2 - g.cx) * 0.55, g.cy + ((e.a.py + e.b.py) / 2 - g.cy) * 0.55, e.b.px, e.b.py);
      c.stroke();
    }
    if (focus && focus.st) {
      c.strokeStyle = focus.st.gone ? AMBER : INK; c.lineWidth = 1.2; c.setLineDash(focus.st.gone ? [3, 3] : []);
      const a = focus.theta; c.beginPath(); c.moveTo(g.cx + Math.cos(a) * (g.rIn - 6), g.cy + Math.sin(a) * (g.rIn - 6)); c.lineTo(focus.px, focus.py); c.stroke(); c.setLineDash([]);
    }
    for (const nd of shown) this.mark(c, nd);
    this.labels(c, g, shown, focus);
    // the article itself, in the middle
    const born = this.center.created <= t;
    c.beginPath(); c.arc(g.cx, g.cy, g.rIn - 7, 0, 2 * Math.PI); c.fillStyle = '#fff'; c.fill();
    c.strokeStyle = born ? INK : RED; c.lineWidth = born ? 1.4 : 1.6; c.setLineDash(born ? [] : [4, 3]); c.stroke(); c.setLineDash([]);
    this.title(c, g, born);
  }

  mark(c, nd) {
    const s = nd.st, r = nd.size, x = nd.px, y = nd.py;
    if (nd.contested) { c.beginPath(); c.arc(x, y, r + 4.5, 0, 2 * Math.PI); c.strokeStyle = AMBER; c.lineWidth = 2; c.stroke(); }
    if (s.gone) {                                    // a link that was removed: a small cross where it used to be
      const k = Math.max(2.6, r * 0.6);
      c.strokeStyle = 'rgba(160,112,0,0.75)'; c.lineWidth = 1.3;
      c.beginPath(); c.moveTo(x - k, y - k); c.lineTo(x + k, y + k); c.moveTo(x + k, y - k); c.lineTo(x - k, y + k); c.stroke();
      return;
    }
    if (s.red) {                                     // linked, but nobody had written it yet: a red link
      c.beginPath(); c.moveTo(x, y - r - 1); c.lineTo(x + r + 1, y); c.lineTo(x, y + r + 1); c.lineTo(x - r - 1, y); c.closePath();
      c.fillStyle = '#fff'; c.fill(); c.strokeStyle = RED; c.lineWidth = 1.7; c.stroke();
      return;
    }
    c.beginPath(); c.arc(x, y, r, 0, 2 * Math.PI);
    if (s.out) { c.fillStyle = nd.outside ? OUTSIDE : LINK; c.fill(); c.strokeStyle = '#fff'; c.lineWidth = 1.5; c.stroke(); }
    else { c.fillStyle = '#fff'; c.fill(); c.strokeStyle = LINK; c.lineWidth = 1.6; c.stroke(); }
    if (s.out && s.inn) { c.beginPath(); c.arc(x, y, r + 2.6, 0, 2 * Math.PI); c.strokeStyle = LINK; c.lineWidth = 1.1; c.stroke(); }
    if (nd === this.hover || nd === this.selected) { c.beginPath(); c.arc(x, y, r + 5.5, 0, 2 * Math.PI); c.strokeStyle = INK; c.lineWidth = 1.5; c.stroke(); }
  }

  labels(c, g, shown, focus) {
    const rank = (nd) => (nd === focus ? 1e9 : 0) + (nd.contested ? 5e5 : 0) + (nd.st.gone ? -1e6 : 0) + (nd.outside ? 0 : 1e3) + (nd.weight || 0) + (nd.st.red ? 200 : 0);
    const want = shown.filter((nd) => nd === focus || !nd.st.gone).sort((a, b) => rank(b) - rank(a)).slice(0, 70);
    const taken = [{ x0: g.cx - g.rIn, y0: g.cy - g.rIn, x1: g.cx + g.rIn, y1: g.cy + g.rIn }, ...this.yearBoxes,
      ...shown.map((nd) => ({ x0: nd.px - nd.size - 1, y0: nd.py - nd.size - 1, x1: nd.px + nd.size + 1, y1: nd.py + nd.size + 1, of: nd }))];
    c.textBaseline = 'middle'; c.lineJoin = 'round';
    for (const nd of want) {
      let text = this.label(nd); if (text.length > 30) text = text.slice(0, 28) + '…';
      const big = nd === focus;
      c.font = `${big ? 600 : 400} ${big ? 13 : 12}px "IBM Plex Sans", sans-serif`;
      const wdt = c.measureText(text).width, right = Math.cos(nd.theta) >= 0, pad = nd.size + (nd.contested ? 8 : 5);
      const x = right ? nd.px + pad : nd.px - pad - wdt, y = nd.py;
      const box = { x0: x - 2, y0: y - 8, x1: x + wdt + 2, y1: y + 8 };
      if (!big && (box.x0 < 4 || box.x1 > this.w - 4 || taken.some((b) => b.of !== nd && box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0))) continue;
      taken.push(box);
      c.textAlign = 'left';
      c.strokeStyle = 'rgba(255,255,255,0.92)'; c.lineWidth = 3.5; c.strokeText(text, x, y);
      c.fillStyle = nd.st.gone ? INK3 : nd.outside ? INK2 : INK; c.fillText(text, x, y);
    }
  }

  title(c, g, born) {
    const words = this.label(this.center).split(' '), max = (g.rIn - 14) * 1.75, lines = [];
    const size = Math.max(13, Math.min(19, g.rIn * 0.27));
    c.font = `600 ${size}px "Libertinus Serif", Georgia, serif`;
    let line = '';
    for (const wd of words) {
      if (line && c.measureText(line + ' ' + wd).width > max) { lines.push(line); line = wd; } else line = line ? line + ' ' + wd : wd;
    }
    lines.push(line);
    const show = lines.slice(0, 3), lh = size * 1.12, top = g.cy - ((show.length - 1) * lh) / 2 - (born ? 0 : 6);
    c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = INK;
    show.forEach((ln, i) => c.fillText(ln, g.cx, top + i * lh));
    if (!born) { c.font = '11px "IBM Plex Sans", sans-serif'; c.fillStyle = RED; c.fillText('not written yet', g.cx, top + show.length * lh); }
  }

  at(e) {
    const box = this.canvas.getBoundingClientRect(), x = e.clientX - box.left, y = e.clientY - box.top;
    let best = null, bestD = 13;
    for (const nd of this.nodes) {
      if (!nd.st) continue;
      const d = Math.hypot(nd.px - x, nd.py - y) - nd.size;
      if (d < bestD) { best = nd; bestD = d; }
    }
    return best;
  }

  move(e) {
    const nd = this.at(e);
    this.canvas.classList.toggle('pick', !!nd);
    if (nd !== this.hover) { this.hover = nd; this.draw(); }
    if (!nd) return Tip.hide();
    Tip.show(e.clientX, e.clientY, this.on.describe(nd, this.t));
  }
}

// ------------------------------------------------------------------ timeline: the article's months, and the handle on time
class Timeline {
  constructor(box, on) {
    this.box = box; this.on = on;
    this.input = h('input', { type: 'range', 'aria-label': 'The day shown', step: String(DAY) });
    this.svg = svgEl('svg', {});
    box.append(this.input, this.svg);
    this.input.addEventListener('input', () => on.time(Number(this.input.value), true));
    this.input.addEventListener('mousemove', (e) => this.hoverAt(e));
    this.input.addEventListener('mouseleave', () => Tip.hide());
    new ResizeObserver(() => this.draw()).observe(box);
  }
  setData(months, t0, t1) {
    this.t0 = t0; this.t1 = t1;
    this.months = months.map((m) => ({ ...m, ms: Date.UTC(Math.floor(m.ym / 100), m.ym % 100 - 1, 1) }));
    this.input.min = String(t0); this.input.max = String(t1);
    this.draw();
  }
  x(ms) { return ((ms - this.t0) / (this.t1 - this.t0)) * this.w; }
  draw() {
    if (!this.months) return;
    const w = (this.w = this.box.clientWidth), svg = this.svg;
    svg.replaceChildren();
    if (!w) return;
    const top = 30, mid = 56, month = 30.44 * DAY, bw = Math.max(1, (month / (this.t1 - this.t0)) * w - 0.6);
    const maxE = Math.max(1, ...this.months.map((m) => m.edits)), maxL = Math.max(1, ...this.months.map((m) => Math.max(m.added, m.removed)));
    const g = svgEl('g', {});
    for (const m of this.months) {
      const x = this.x(m.ms);
      if (x < -bw || x > w) continue;
      if (m.edits) { const hh = Math.max(1, 27 * Math.sqrt(m.edits / maxE)); g.append(svgEl('rect', { x, y: top - hh, width: bw, height: hh, fill: '#aab3be' })); }
      if (m.added) { const hh = Math.max(1, 17 * Math.sqrt(m.added / maxL)); g.append(svgEl('rect', { x, y: mid - hh, width: bw, height: hh, fill: LINK })); }
      if (m.removed) { const hh = Math.max(1, 13 * Math.sqrt(m.removed / maxL)); g.append(svgEl('rect', { x, y: mid + 1, width: bw, height: hh, fill: AMBER })); }
    }
    g.append(svgEl('line', { x1: 0, x2: w, y1: top + 0.5, y2: top + 0.5, stroke: RULE }), svgEl('line', { x1: 0, x2: w, y1: mid + 0.5, y2: mid + 0.5, stroke: RULE }));
    const axis = svgEl('g', { class: 'axis' });
    const y0 = new Date(this.t0).getUTCFullYear() + 1, y1 = new Date(this.t1).getUTCFullYear(), step = w / (y1 - y0 + 1) < 34 ? 2 : 1;
    for (let y = y0; y <= y1; y++) {
      const x = this.x(Date.UTC(y, 0, 1));
      axis.append(svgEl('line', { x1: x, x2: x, y1: mid + 15, y2: mid + 19, stroke: '#c0c8d2' }));
      if ((y - y0) % step === 0 && x < w - 16) { const tx = svgEl('text', { x: x + 3, y: mid + 27 }); tx.textContent = y; axis.append(tx); }
    }
    this.cursor = svgEl('g', { class: 'cursor' });
    this.cursor.append(svgEl('line', { x1: 0, x2: 0, y1: -2, y2: mid + 16 }), svgEl('circle', { cx: 0, cy: -2, r: 5 }));
    svg.append(g, axis, this.cursor);
    this.setTime(this.t ?? this.t1);
  }
  setTime(t) {
    this.t = t;
    if (document.activeElement !== this.input || Number(this.input.value) !== t) this.input.value = String(t);
    if (this.cursor) this.cursor.setAttribute('transform', `translate(${this.x(t)},0)`);
  }
  monthAt(ms) { const d = new Date(ms), ym = d.getUTCFullYear() * 100 + d.getUTCMonth() + 1; return this.months.find((m) => m.ym === ym) || { ym, edits: 0, reverts: 0, added: 0, removed: 0, ms: Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) }; }
  hoverAt(e) {
    const box = this.box.getBoundingClientRect(), ms = this.t0 + ((e.clientX - box.left) / box.width) * (this.t1 - this.t0), m = this.monthAt(ms);
    Tip.show(e.clientX, box.top - 96, [h('b', null, monthYear(m.ms)),
      h('p', null, h('span', { class: 'k', style: 'background:#aab3be' }), `${plural(m.edits, 'edit')}${m.reverts ? `, ${plural(m.reverts, 'revert')}` : ''}`),
      h('p', null, h('span', { class: 'k', style: `background:${LINK}` }), `${plural(m.added, 'link')} added`),
      h('p', null, h('span', { class: 'k', style: `background:${AMBER}` }), `${plural(m.removed, 'link')} removed`)]);
  }
}

// ------------------------------------------------------------------ explore: one article
const START = 1256;       // Talvisota, the Winter War: where a first visit begins
const Explore = {
  id: null, trail: [],

  async open(id, params) {
    const first = this.id === null;
    if (id !== this.id || !$('.explore')) {
      this.id = id; this.mode = null; this.stop();
      view.replaceChildren(h('p', { class: 'loading' }, 'Reading the history…'));
      let page, graph;
      try {
        [page, graph] = await Promise.all([api(`/api/page/${id}`, 'This article: its numbers and its months'), api(`/api/page/${id}/graph`, 'Its connections, over its whole life')]);
      } catch (e) { view.replaceChildren(h('p', { class: 'error' }, `That article is not in the graph. ${e.message}`)); return; }
      if (id !== this.id) return;
      this.page = page; this.graph = graph;
      document.title = `${page.title}: its connections through time. Rabbit Hole`;
      const seen = this.trail.findIndex((x) => x.id === id);
      this.trail = seen >= 0 ? this.trail.slice(0, seen + 1) : [...this.trail.slice(-5), { id, title: page.title }];
      this.frame();
      this.rings.setGraph(graph);
      this.t0 = this.rings.t0; this.t1 = graph.end;
      this.timeline.setData(page.timeline, this.t0, this.t1);
      this.t = null;
    }
    const wanted = params.get('t') ? Math.max(this.t0, Math.min(this.t1, toMs(params.get('t')))) : null;
    const intro = first && !wanted && !params.get('e') && !reducedMotion;
    this.setTime(wanted ?? (intro ? this.t0 : this.t ?? this.t1), false);
    if (params.get('e')) this.showEdge(this.id, params.get('e')); else if (params.get('leads')) this.showLeads(); else this.showArticle();
    if (intro) this.play();
  },

  frame() {
    const canvas = h('canvas', { role: 'img', 'aria-label': `The connections of ${this.page.title} as rings: the further out, the later the link was made. The panel beside it lists the same links.` });
    const names = (which, text) => h('button', { type: 'button', 'aria-pressed': String((this.names || 'en') === which), onclick: (e) => {
      this.names = this.rings.names = which; e.target.parentElement.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === e.target))); this.rings.draw(); } }, text);
    const key = (svg, text) => h('div', null, (() => { const s = svgEl('svg', { width: 18, height: 14, viewBox: '0 0 18 14' }); s.innerHTML = svg; return s; })(), text);
    this.trailEl = h('nav', { class: 'trail', 'aria-label': 'The way you came' });
    this.noteEl = h('p', { class: 'note' });
    const plate = h('div', { class: 'plate' }, this.trailEl, this.noteEl, canvas,
      // open on a wide screen; on a phone it starts as its one sentence, so it does not cover the picture
      Object.assign(h('details', { class: 'legend' },
        h('summary', null, 'Rings are years. The further out, the later the link was made.'),
        h('div', { class: 'keys' },
          key(`<circle cx="9" cy="7" r="4.5" fill="${LINK}"/>`, 'this article links to it'),
          key(`<circle cx="9" cy="7" r="4" fill="#fff" stroke="${LINK}" stroke-width="1.6"/>`, 'it links to this article'),
          key(`<path d="M9 1.5 14.5 7 9 12.5 3.5 7Z" fill="#fff" stroke="${RED}" stroke-width="1.6"/>`, 'linked, not yet written'),
          key(`<path d="M5.5 3.5l7 7m0-7l-7 7" stroke="#a07000" stroke-width="1.4"/>`, 'link removed'),
          key(`<circle cx="9" cy="7" r="3" fill="${LINK}"/><circle cx="9" cy="7" r="6" fill="none" stroke="${AMBER}" stroke-width="1.8"/>`, 'added and removed again and again'))),
        { open: window.innerWidth > 980 }),
      h('div', { class: 'plate-tools' }, 'Titles', h('div', { class: 'seg' }, names('en', 'English'), names('fi', 'Suomi'))));
    this.dayEl = h('span', { class: 'day' }); this.whatEl = h('span', { class: 'what' });
    this.playBtn = h('button', { class: 'play', type: 'button', onclick: () => (this.playing ? this.stop() : this.play()) });
    const chart = h('div', { class: 'chart' });
    this.panel = h('aside', { class: 'panel', 'aria-live': 'off' });
    view.replaceChildren(h('div', { class: 'explore' }, plate,
      h('div', { class: 'time' }, this.playBtn, h('div', { class: 'when' }, this.dayEl, this.whatEl), h('span'), chart), this.panel));
    this.rings = new Rings(canvas, {
      pick: (nd) => { this.stop(); location.hash = `#/a/${this.id}?t=${iso(this.t)}&e=${encodeURIComponent(nd.out ? nd.title : this.page.title)}${nd.out ? '' : `&from=${nd.id}`}`; },
      open: (nd) => { location.hash = `#/a/${nd.id}`; },
      describe: (nd, t) => this.describe(nd, t),
    });
    this.rings.names = this.names || 'en';
    this.timeline = new Timeline(chart, { time: (t) => { this.stop(); this.setTime(t, true); } });
    this.paintPlay();
    this.trailEl.replaceChildren(...this.trail.flatMap((x, i) => [i ? h('span', { class: 'sep' }, '›') : null,
      i === this.trail.length - 1 ? h('span', { class: 'here' }, x.title) : h('a', { href: `#/a/${x.id}` }, x.title)]).filter(Boolean));
    const tt = this.graph.totals;
    this.noteEl.textContent = tt.out_ever + tt.in_ever > tt.shown
      ? `The picture shows ${n(tt.shown)} connections: a share of every year’s new ones. In all, this article has linked to ${n(tt.out_ever)} others and ${n(tt.in_ever)} have linked to it.` : '';
  },

  describe(nd, t) {
    const lines = [];
    const tell = (periods, made, undone) => {
      if (!periods) return;
      const cur = periods.find((p) => p[0] <= t && (p[1] == null || p[1] > t));
      const past = periods.filter((p) => p[1] != null && p[1] <= t);
      if (cur) lines.push(`${made} since ${longDay(cur[0])}`);
      else if (past.length) { const last = past[past.length - 1]; lines.push(`${undone} on ${longDay(last[1])}, after ${howLong(last[1] - last[0])}`); }
      if (periods.length > 1) lines.push(`Added ${periods.length} times in all`);
    };
    tell(nd.out, 'This article links to it', 'This article dropped the link');
    tell(nd.in, 'It links to this article', 'It dropped its link to this article');
    if (!nd.outside && nd.created > t) lines.push(`Nobody had written it yet: it was started on ${longDay(nd.created)}`);
    if (nd.outside) lines.push('Outside the History of Finland set: no history loaded for it');
    return [h('b', null, nd.title), nd.en && nd.en !== nd.title ? h('i', null, nd.en) : null, ...lines.map((l) => h('p', null, l)),
      h('p', { class: 'faint' }, nd.outside ? 'Click for the life of this link' : 'Click for the life of this link, double-click to go there')];
  },

  // time
  setTime(t, fromUser) {
    this.t = t;
    this.rings.setTime(t); this.timeline.setTime(t);
    this.dayEl.textContent = longDay(t);
    const m = this.timeline.monthAt(t);
    const key = (colour, text) => h('span', null, h('span', { class: 'k', style: `background:${colour}` }), text);
    this.whatEl.replaceChildren(`In ${monthYear(m.ms)}:`, key('#aab3be', plural(m.edits, 'edit')), key(LINK, `${plural(m.added, 'link')} added`), key(AMBER, `${n(m.removed)} removed`),
      h('span', { class: 'faint' }, 'Bars: each month, square-root scale'));
    if (this.mode === 'article') (this.playing ? this.asofSoon : this.asofLater)();
    if (fromUser) this.remember();
  },
  remember: debounce(function () { if (Explore.mode === 'article') history.replaceState(null, '', `#/a/${Explore.id}?t=${iso(Explore.t)}`); }, 400),
  play() {
    if (this.t >= this.t1 - DAY) this.t = this.t0;
    this.playing = true; this.paintPlay();
    const from = this.t, span = this.t1 - from, took = 16000 * (span / (this.t1 - this.t0)) + 1500, began = performance.now();
    const step = (now) => {
      if (!this.playing) return;
      const k = Math.min(1, (now - began) / took);
      this.setTime(from + span * k, false);
      if (k < 1) this.raf = requestAnimationFrame(step); else { this.stop(); this.remember(); }
    };
    this.raf = requestAnimationFrame(step);
  },
  stop() { if (this.playing) { this.playing = false; cancelAnimationFrame(this.raf); this.paintPlay(); if (this.mode === 'article') this.asof(); } },
  paintPlay() {
    if (!this.playBtn) return;
    this.playBtn.setAttribute('aria-label', this.playing ? 'Pause' : 'Play the article’s history');
    this.playBtn.innerHTML = this.playing ? '<svg width="14" height="14" viewBox="0 0 14 14"><rect x="2" y="1" width="3.5" height="12"/><rect x="8.5" y="1" width="3.5" height="12"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 14 14"><path d="M3 1l10 6-10 6z"/></svg>';
  },

  // panel: the article on the chosen day
  showArticle() {
    this.mode = 'article'; this.rings.selected = null; this.rings.draw();
    const p = this.page;
    const also = [p.title_en && p.title_en !== p.title ? p.title_en : null, p.title_sv && p.title_sv !== p.title ? `${p.title_sv} (sv)` : null].filter(Boolean).join(', ');
    this.leadMode = this.leadMode || 'then';
    const leadSeg = (which, text) => h('button', { type: 'button', 'aria-pressed': String(this.leadMode === which), onclick: () => { this.leadMode = which; this.showArticle(); } }, text);
    this.el = {
      on: h('span', { class: 'on' }), lead: h('p', { class: 'lead' }), foot: h('p', { class: 'lead-foot' }),
      facts: h('div', { class: 'facts' }), since: h('div'), near: h('div'), said: h('p', { class: 'said', hidden: true }),
    };
    const explain = h('button', { class: 'btn', type: 'button', onclick: () => { this.el.said.hidden = false; say(this.el.said, '/api/explain', { kind: 'change', page_id: this.id, when: iso(this.t) }); } }, 'Describe the change in words');
    const target = h('input', { type: 'text', placeholder: 'another article', 'aria-label': 'Another article' }), list = h('ul', { class: 'suggest', hidden: true }), route = h('div', { class: 'route' });
    suggestBox(target, list, async (it) => {
      route.textContent = 'Looking…';
      const [then, now] = await Promise.all([api(`/api/path?from=${this.id}&to=${it.page_id}&t=${iso(this.t)}`, `The way to ${it.title} that day`), api(`/api/path?from=${this.id}&to=${it.page_id}`, `The way to ${it.title} today`)]);
      const line = (r) => h('p', null, h('b', null, `${longDay(r.as_of)}: `), r.clicks ? [`${plural(r.clicks, 'click')}. `, r.paths[0].flatMap((x, i) => [i ? h('span', { class: 'hop' }, '›') : null, h('a', { href: `#/a/${x.page_id}` }, x.title)])] : 'no way in three clicks or fewer.');
      route.replaceChildren(line(then), then.as_of === now.as_of ? null : line(now));
    });
    this.panel.replaceChildren(
      h('h1', null, p.title), also ? h('p', { class: 'aka' }, also) : null,
      h('p', { class: 'links-out' }, h('a', { href: p.url, target: '_blank', rel: 'noopener' }, 'Read it on Wikipedia'),
        h('a', { href: `#/a/${this.id}?leads=1` }, `Its opening, rewritten ${n(p.lead_versions)} times`)),
      this.el.facts,
      h('div', { class: 'lead-box' },
        h('div', { class: 'lead-head' }, this.el.on, h('div', { class: 'seg' }, leadSeg('then', 'That day'), leadSeg('diff', 'Changes since'))),
        this.el.lead, h('button', { class: 'more', type: 'button', onclick: (e) => { this.leadOpen = !this.leadOpen; this.el.lead.classList.toggle('open', this.leadOpen); e.target.textContent = this.leadOpen ? 'Show less' : 'Read the whole opening'; } }, this.leadOpen ? 'Show less' : 'Read the whole opening'),
        this.el.foot),
      h('section', null, h('h2', null, 'Since that day'), this.el.since, h('p', { style: 'margin-top:10px' }, explain), this.el.said),
      h('section', null, h('h2', null, 'Close in meaning', h('span', { class: 'sub' }, 'does this article link to them?')), this.el.near),
      p.contested.length ? h('section', null, h('h2', null, 'Connections people fought over'), h('ul', { class: 'contested' }, p.contested.map((c) =>
        h('li', null, h('a', { href: `#/a/${c.src}?e=${encodeURIComponent(c.target)}` }, `${c.src_title} › ${c.target}`),
          h('span', { class: 'meta' }, `added ${n(c.adds)} times, removed ${n(c.removals)}`))))) : null,
      h('section', null, h('h2', null, 'Get from here to', h('span', { class: 'sub' }, 'by following links, that day and today')), h('div', { class: 'pathform' }, target, list), route));
    this.asof();
  },
  asofLater: debounce(() => Explore.asof(), 170),
  asofSoon: (() => { let last = 0; return () => { const now = performance.now(); if (now - last > 650) { last = now; Explore.asof(true); } }; })(),

  async asof(light) {
    if (this.mode !== 'article') return;
    const id = this.id, day = iso(this.t), mine = (this.asofSeq = (this.asofSeq || 0) + 1);
    Sql.calls = Sql.calls.filter((c) => !c.label.startsWith('That day'));
    const jobs = [api(`/api/page/${id}/asof?t=${day}`, `That day (${day}): the opening, the links, and what changed since`)];
    if (!light) jobs.push(api(`/api/page/${id}/neighbours?t=${day}`, `That day (${day}): close in meaning, and linked or not`));
    let a, near;
    try { [a, near] = await Promise.all(jobs); } catch (e) { return; }
    if (mine !== this.asofSeq || id !== this.id || this.mode !== 'article') return;
    const el = this.el, isNow = this.t >= this.t1 - DAY;
    el.on.textContent = isNow ? 'Today' : `On ${longDay(a.as_of)}`;
    el.lead.className = this.leadOpen ? 'lead open' : 'lead';
    if (!a.exists) { el.lead.className = 'lead none'; el.lead.textContent = 'Nobody had written this article yet. Other articles already linked to it.'; el.foot.textContent = ''; }
    else if (!a.lead) { el.lead.className = 'lead none'; el.lead.textContent = 'The article opened with no running text that day: only a table, an infobox or a redirect.'; el.foot.textContent = ''; }
    else {
      if (this.leadMode === 'diff' && a.lead_diff) el.lead.replaceChildren(...a.lead_diff.flatMap(([op, text]) => [op === '=' ? text : h(op === '+' ? 'ins' : 'del', null, text), ' ']));
      else el.lead.textContent = this.leadMode === 'diff' ? a.lead : a.lead;
      el.foot.replaceChildren(this.leadMode === 'diff' ? (a.lead_diff ? 'Yellow was there that day and is gone; blue has been added since. ' : 'The opening has not changed since. ') : '',
        `This opening had stood since ${longDay(a.lead_since)}. `, h('a', { href: a.lead_url, target: '_blank', rel: 'noopener' }, 'See that version on Wikipedia'));
    }
    el.facts.replaceChildren(
      h('div', null, h('b', null, n(a.links_out)), h('span', null, isNow ? 'links in it' : 'links in it that day')),
      h('div', null, h('b', null, n(a.links_in)), h('span', null, 'articles linking to it')),
      h('div', null, h('b', null, n(a.versions)), h('span', null, isNow ? 'saved versions' : 'versions by then')));
    const chips = (rows) => h('div', { class: 'chips' }, rows.slice(0, 60).map((r) => r.dst ? h('a', { class: 'chip', href: `#/a/${r.dst}` }, r.target)
      : h('a', { class: 'chip out', href: `#/a/${id}?t=${day}&e=${encodeURIComponent(r.target)}` }, r.target)), rows.length > 60 ? h('span', { class: 'faint' }, `and ${n(rows.length - 60)} more`) : null);
    if (isNow) el.since.replaceChildren(h('p', { class: 'quiet' }, 'This is the article as the data ends, on 1 October 2026. Drag the handle below to go back.'));
    else {
      const box = h('div');
      const pill = (cls, count, text, rows) => h('button', { class: `pill ${cls}`, type: 'button', onclick: () => box.replaceChildren(chips(rows)) }, h('b', null, n(count)), text);
      el.since.replaceChildren(h('div', { class: 'since' }, pill('added', a.new.length, 'links added', a.new), pill('removed', a.gone.length, 'links removed', a.gone)), box);
    }
    if (!near && !el.near.childNodes.length) el.near.replaceChildren(h('p', { class: 'quiet' }, 'Pause to compare: the closest articles by meaning, and whether this one linked to them that day.'));
    if (near) {
      const rows = near.neighbours.slice(0, this.nearAll ? 24 : 9);
      const tag = (r) => r.linked_now ? h('span', { class: 'tag yes' }, r.linked_then || isNow ? 'linked' : 'linked since') : r.linked_ever ? h('span', { class: 'tag was' }, r.linked_then ? 'linked then, removed' : 'was linked') : h('span', { class: 'tag never' }, 'never linked');
      el.near.replaceChildren(near.neighbours.length ? h('ul', { class: 'near' }, rows.map((r) => h('li', null,
        h('a', { class: 't', href: `#/a/${r.page_id}` }, r.title), tag(r), r.title_en && r.title_en !== r.title ? h('span', { class: 'en' }, r.title_en) : null)))
        : h('p', { class: 'quiet' }, 'This article has no vector in dataset S, so there is nothing to compare.'),
      near.neighbours.length > 9 && !this.nearAll ? h('button', { class: 'more', type: 'button', onclick: () => { this.nearAll = true; this.asof(); } }, 'Show more') : null);
    }
  },

  // panel: one connection's whole life
  async showEdge(src, target) {
    this.mode = 'edge';
    const from = new URLSearchParams(location.hash.split('?')[1] || '').get('from');
    if (from) { src = Number(from); target = this.page.title; }
    this.panel.replaceChildren(h('p', { class: 'loading' }, 'Reading the link’s history…'));
    let e;
    try { e = await api(`/api/edge?src=${src}&target=${encodeURIComponent(target)}`, `One connection: ${target}`); } catch (err) { this.panel.replaceChildren(this.backBtn(), h('p', { class: 'error' }, err.message)); return; }
    if (this.mode !== 'edge') return;
    const other = src === this.id ? (e.dst ? e.dst.page_id : 't:' + target) : src;
    this.rings.selected = this.rings.nodes.find((nd) => nd.id === other) || null; this.rings.draw();
    const who = (name, anon) => (anon ? 'an unregistered editor' : name || 'someone');
    const t0 = this.t0, t1 = this.t1, life = svgEl('svg', { viewBox: '0 0 400 34', preserveAspectRatio: 'none' });
    life.append(svgEl('line', { x1: 0, x2: 400, y1: 12, y2: 12, stroke: RULE, 'stroke-width': 2 }));
    for (const p of e.periods) {
      const x0 = ((p.added_ms - t0) / (t1 - t0)) * 400, x1 = (((p.removed_ms ?? t1) - t0) / (t1 - t0)) * 400;
      life.append(svgEl('rect', { x: x0, y: 7, width: Math.max(2.5, x1 - x0), height: 10, rx: 2, fill: LINK }));
    }
    const sum = `${e.times_added === 1 ? 'Added once' : `Added ${n(e.times_added)} times`}${e.times_removed ? ` and removed ${e.times_removed === 1 ? 'once' : n(e.times_removed) + ' times'}` : ''}${e.registered_editors > 1 ? `, by ${n(e.registered_editors)} registered editors` : ''}. Today the link is ${e.alive ? 'there' : 'gone'}.`;
    const said = h('p', { class: 'said', hidden: true });
    this.panel.replaceChildren(this.backBtn(),
      h('h1', { class: 'edge-title' }, h('a', { href: `#/a/${e.src.page_id}` }, e.src.title), h('span', { class: 'to' }, '›'), e.dst ? h('a', { href: `#/a/${e.dst.page_id}` }, target) : target),
      h('p', { class: 'edge-sum' }, sum),
      h('div', { class: 'lifeline', role: 'img', 'aria-label': 'When the link existed' }, life,
        h('div', { style: 'display:flex;justify-content:space-between;font-size:11px;color:var(--ink-3)' }, h('span', null, yearOf(t0)), h('span', null, 'blue: the link was there'), h('span', null, yearOf(t1)))),
      h('p', { style: 'margin-top:12px' }, h('button', { class: 'btn solid', type: 'button', onclick: () => { said.hidden = false; say(said, '/api/explain', { kind: 'edge', src, target }); } }, e.times_removed > 1 ? 'Explain the disagreement' : 'Explain this link')),
      said,
      h('ol', { class: 'periods' }, e.periods.map((p) => h('li', { class: p.removed ? 'ended' : '' },
        h('div', { class: 'ev ad' }, h('b', null, 'Added'), h('a', { href: p.add_url, target: '_blank', rel: 'noopener', title: 'See this edit on Wikipedia' }, longDay(p.added_ms)),
          h('span', { class: 'who' }, `by ${who(p.added_by, p.added_anonymous)}`), p.add_comment ? h('span', { class: 'why' }, `“${p.add_comment}”`) : null),
        h('blockquote', null, contextNodes(p.context)),
        p.removed ? h('div', { class: 'ev rm' }, h('b', null, 'Removed'), h('a', { href: p.remove_url, target: '_blank', rel: 'noopener', title: 'See this edit on Wikipedia' }, longDay(p.removed_ms)),
          h('span', { class: 'who' }, `by ${who(p.removed_by, p.removed_anonymous)}`), p.remove_comment ? h('span', { class: 'why' }, `“${p.remove_comment}”`) : null) : null,
        h('p', { class: 'stood' }, p.removed ? `It stood for ${howLong(p.removed_ms - p.added_ms)}. ` : `It has stood for ${howLong(t1 - p.added_ms)}. `,
          src === this.id || from ? h('button', { class: 'jump', type: 'button', onclick: () => { this.stop(); this.setTime(Math.min(t1, p.added_ms + DAY), false); } }, 'Show that day in the picture') : null)))));
  },
  backBtn() { return h('button', { class: 'back', type: 'button', onclick: () => { location.hash = `#/a/${this.id}?t=${iso(this.t)}`; } }, `‹ Back to ${this.page.title}`); },

  // panel: the opening paragraphs through time
  async showLeads() {
    this.mode = 'leads';
    this.panel.replaceChildren(h('p', { class: 'loading' }, 'Reading every version of the opening…'));
    const d = await api(`/api/page/${this.id}/leads`, 'Every version of the opening paragraphs');
    if (this.mode !== 'leads') return;
    this.panel.replaceChildren(this.backBtn(), h('h1', { class: 'edge-title' }, 'How the article opened'),
      h('p', { class: 'edge-sum' }, `The opening of ${this.page.title} has been rewritten ${n(d.versions)} times. These ${n(d.shown.length)} versions each stood for a month or more.`),
      h('ol', { class: 'periods' }, d.shown.map((v) => h('li', { class: v.until ? 'ended' : '' },
        h('div', { class: 'ev' }, h('b', null, longDay(v.since_ms)), h('span', { class: 'who' }, v.until ? `stood for ${howLong(v.until_ms - v.since_ms)}` : 'stands today'),
          h('a', { href: v.url, target: '_blank', rel: 'noopener' }, 'on Wikipedia')),
        h('blockquote', null, v.text.length > 420 ? v.text.slice(0, 420) + '…' : v.text),
        h('p', { class: 'stood' }, h('button', { class: 'jump', type: 'button', onclick: () => { this.stop(); this.setTime(Math.max(this.t0, v.since_ms + DAY), false); } }, 'Show that day in the picture'))))));
  },
};

// ------------------------------------------------------------------ pages
function page(cls, ...kids) {
  const el = h('div', { class: `page ${cls || ''}` }, h('div', { class: 'in' }, kids));
  view.replaceChildren(el);
  const title = el.querySelector('h1');
  if (title) document.title = `${title.textContent}. Rabbit Hole`;
  return el;
}
const cell = (text, cls) => h('td', { class: cls }, text);

async function showDisputes() {
  page('', h('p', { class: 'loading' }, 'Looking for disagreements…'));
  const d = await api('/api/disputes', 'Contested connections and rewritten openings');
  page('',
    h('h1', null, 'Connections people fought over'),
    h('p', { class: 'intro' }, 'A link is a claim: this matters to that. Some claims do not settle. Each of these links was added by at least two registered editors and removed by at least two, in edits that were about that link.'),
    h('table', { class: 'list' },
      h('thead', null, h('tr', null, h('th', null, 'Article and the link in question'), h('th', { class: 'n' }, 'Added'), h('th', { class: 'n' }, 'Removed'), h('th', { class: 'n' }, 'Editors adding'), h('th', { class: 'n' }, 'Editors removing'), h('th', null, 'Years'), h('th', null, 'Today'))),
      h('tbody', null, d.connections.map((c) => h('tr', null,
        h('td', null, h('a', { class: 't', href: `#/a/${c.src}?e=${encodeURIComponent(c.target)}` }, c.src_title, h('span', { class: 'to' }, '›'), c.target),
          c.src_en || c.target_en ? h('span', { class: 'en' }, `${c.src_en || c.src_title} › ${c.target_en || c.target}`) : null),
        cell(n(c.adds), 'n'), cell(n(c.removals), 'n'), cell(n(c.adders), 'n'), cell(n(c.removers), 'n'),
        cell(yearOf(c.first_at) === yearOf(c.last_at) ? yearOf(c.first_at) : `${yearOf(c.first_at)} to ${yearOf(c.last_at)}`, 'nw'), cell(c.alive ? 'linked' : 'not linked', 'nw'))))),
    h('h2', null, 'Openings rewritten most often'),
    h('p', { class: 'how' }, 'The first paragraphs say what an article is about. Every version of them is kept, so the framing can be read back year by year.'),
    h('table', { class: 'list' },
      h('thead', null, h('tr', null, h('th', null, 'Article'), h('th', { class: 'n' }, 'Versions of the opening'), h('th', { class: 'n' }, 'Saved versions'), h('th', { class: 'n' }, 'Reverts'), h('th', { class: 'n' }, 'Editors'))),
      h('tbody', null, d.rewritten.map((r) => h('tr', null,
        h('td', null, h('a', { class: 't', href: `#/a/${r.page_id}?leads=1` }, r.title), r.title_en && r.title_en !== r.title ? h('span', { class: 'en' }, r.title_en) : null),
        cell(n(r.lead_versions), 'n'), cell(n(r.versions), 'n'), cell(n(r.reverts), 'n'), cell(n(r.editors), 'n'))))));
}

const GAPS = {
  sv: { name: 'Not in Swedish', title: 'Central in Finnish, missing in Swedish',
    intro: 'Articles that many others link to in the Finnish graph, with no Swedish article on the same topic in the dataset. The more links point at an article, the more a reader of the other language is missing.',
    caveat: 'Dataset S is a snapshot of August 2025 and can lack an article that exists. Check Wikidata before starting a translation.' },
  en: { name: 'Not in English', title: 'Central in Finnish, missing in English',
    intro: 'Articles that many others link to in the Finnish graph, with no English article on the same topic in the dataset.',
    caveat: 'Dataset S is a snapshot of August 2025 and can lack an article that exists. Check Wikidata before starting a translation.' },
  waited: { name: 'Linked before written', title: 'Linked long before anyone wrote them',
    intro: 'Articles that other articles pointed at before they existed: red links that turned blue. How long each waited, and how many articles were waiting.',
    caveat: 'A title that was a redirect before it became an article looks the same in this data. Treat a single row as a lead, not a fact.' },
  wanted: { name: 'Wanted now', title: 'Linked today, written by nobody',
    intro: 'Titles that several articles link to today and that no page on Finnish Wikipedia answers to. Open a row to read what the linking articles already say about it.',
    caveat: null },
  orphans: { name: 'No way in', title: 'No way in',
    intro: 'Articles of the set that no other article of the set links to, longest first. A reader only finds them by searching.', caveat: null },
};

async function showGaps(kind) {
  kind = GAPS[kind] ? kind : 'sv';
  page('', h('p', { class: 'loading' }, 'Looking for gaps…'));
  const d = await api(`/api/gaps?kind=${kind}`, `Gaps: ${GAPS[kind].name.toLowerCase()}`), meta = GAPS[kind], rows = d.rows;
  const link = (r) => h('a', { class: 't', href: `#/a/${r.page_id}` }, r.title);
  const en = (r) => (r.title_en && r.title_en !== r.title ? h('span', { class: 'en' }, r.title_en) : null);
  let head, body, caveat = meta.caveat;
  if (kind === 'sv' || kind === 'en') {
    head = ['Article', ['Articles linking to it', 'n'], ['Size', 'n'], 'Check'];
    body = rows.map((r) => h('tr', null, h('td', null, link(r), r.other_title ? h('span', { class: 'en' }, `${kind === 'sv' ? 'in English' : 'in Swedish'}: ${r.other_title}`) : null),
      cell(n(r.links_in), 'n'), cell(r.bytes ? `${n(Math.round(r.bytes / 1000))} kB` : '', 'n'),
      h('td', null, h('a', { href: `https://www.wikidata.org/wiki/${r.wikidata_id}`, target: '_blank', rel: 'noopener' }, 'Wikidata'))));
  } else if (kind === 'waited') {
    head = ['Article', ['Articles that linked first', 'n'], 'First linked', 'Written', ['Years waited', 'n']];
    body = rows.map((r) => h('tr', null, h('td', null, link(r), en(r)), cell(n(r.linked_from), 'n'), cell(longDay(r.first_wanted)), cell(longDay(r.written)), cell(r.years_waited, 'n')));
  } else if (kind === 'wanted') {
    if (!d.counts.wanted) caveat = 'This list needs every title of Finnish Wikipedia, redirects included. Run “python pipeline/download.py --titles”, then “python pipeline/load.py titles” and “python pipeline/analyze.py”.';
    else if (!d.counts.all_titles) caveat = 'Built from dataset L alone, which has articles but no redirects or disambiguation pages, so many of these titles do exist. Wikimedia’s title list gives a clean result: “python pipeline/download.py --titles”, then load and analyze again.';
    head = ['Title nobody has written', ['Articles linking to it', 'n'], 'Wanted since', ''];
    body = rows.map((r) => {
      const more = h('td', { colspan: 4, hidden: true });
      const row = h('tr', null, h('td', null, h('span', { class: 't' }, r.target)), cell(n(r.linked_from), 'n'), cell(longDay(r.since)),
        h('td', null, h('button', { class: 'more', type: 'button', onclick: async () => {
          more.hidden = !more.hidden;
          if (more.hidden || more.childNodes.length) return;
          const m = await api('/api/wanted?target=' + encodeURIComponent(r.target), `What the encyclopedia already says about ${r.target}`);
          more.replaceChildren(h('ul', { class: 'mentions' }, m.mentions.map((x) => h('li', null, h('b', null, h('a', { href: `#/a/${x.page_id}` }, x.title)), contextNodes(x.context)))));
        } }, 'What is said about it')));
      return [row, h('tr', null, more)];
    });
  } else {
    head = ['Article', ['Size', 'n'], ['Saved versions', 'n'], 'Created'];
    body = rows.map((r) => h('tr', null, h('td', null, link(r), en(r)), cell(r.bytes ? `${n(Math.round(r.bytes / 1000))} kB` : '', 'n'), cell(n(r.versions), 'n'), cell(longDay(r.created))));
  }
  page('',
    h('h1', null, meta.title), h('p', { class: 'intro' }, meta.intro),
    h('nav', { class: 'kinds', 'aria-label': 'Kinds of gap' }, Object.entries(GAPS).map(([k, g]) => h('a', { href: `#/gaps/${k}`, 'aria-current': String(k === kind) }, g.name, h('span', { class: 'n' }, n(d.counts[k]))))),
    caveat ? h('p', { class: 'caveat' }, caveat) : null,
    h('table', { class: 'list' }, h('thead', null, h('tr', null, head.map((c) => (Array.isArray(c) ? h('th', { class: c[1] }, c[0]) : h('th', null, c))))), h('tbody', null, body)));
}

async function showBigPicture() {
  page('', h('p', { class: 'loading' }, 'Reading the whole graph…'));
  const d = await api('/api/overview'), s = d.stat, years = d.years;
  const yrEl = h('span', { class: 'yr' }), slider = h('input', { type: 'range', min: years[0].yr, max: years[years.length - 1].yr, step: 1, 'aria-label': 'Year' });
  const hubs = svgEl('svg', { viewBox: '0 0 560 430', role: 'img' }), growth = svgEl('svg', { viewBox: '0 0 460 250', role: 'img' }), live = h('p', { class: 'live' });
  const ROW = 42;
  const top = Math.max(...d.hubs.map((x) => x.links_in));
  const bars = new Map();          // one bar per article, kept between years so it can slide
  function paint(yr) {
    yrEl.textContent = yr;
    const rows = d.hubs.filter((x) => x.yr === yr), ids = new Set(rows.map((x) => x.page_id));
    for (const [id, b] of bars) if (!ids.has(id)) { b.g.remove(); bars.delete(id); }
    rows.forEach((x, i) => {
      let b = bars.get(x.page_id);
      if (!b) {
        b = { g: svgEl('g', {}), rect: svgEl('rect', { x: 0, y: 6, height: 12, rx: 3, fill: LINK }), name: svgEl('text', { class: 'name', x: 0, y: 0 }), val: svgEl('text', { class: 'val', y: 16.5 }) };
        b.name.textContent = x.title_en && x.title_en !== x.title ? `${x.title} (${x.title_en})` : x.title;
        const link = svgEl('a', { href: `#/a/${x.page_id}?t=${yr}-12-31` }); link.append(b.rect, b.name, b.val);
        b.g.append(link); b.g.style.transition = reducedMotion ? '' : 'transform .45s ease'; b.rect.style.transition = reducedMotion ? '' : 'width .45s ease';
        hubs.append(b.g); bars.set(x.page_id, b);
        b.g.style.transform = `translate(0px, ${16 + i * ROW}px)`;
      }
      const w = Math.max(2, (x.links_in / top) * 480);
      b.g.style.transform = `translate(0px, ${16 + i * ROW}px)`;
      b.rect.setAttribute('width', w); b.rect.style.width = w + 'px';
      b.val.setAttribute('x', w + 6); b.val.textContent = n(x.links_in);
    });
    const yrow = years.find((y) => y.yr === yr);
    mark.setAttribute('transform', `translate(${gx(yr)},0)`);
    dot.setAttribute('cy', gy(yrow.links));
    tag.textContent = `${n(yrow.links)} links, ${n(yrow.pages)} articles`;
    tag.setAttribute('text-anchor', gx(yr) > 300 ? 'end' : 'start'); tag.setAttribute('x', gx(yr) > 300 ? -8 : 8); tag.setAttribute('y', gy(yrow.links) - 8);
  }
  // growth: links alive at the end of each year
  const maxL = Math.max(...years.map((y) => y.links)), gx = (yr) => 46 + ((yr - years[0].yr) / (years.length - 1)) * 400, gy = (v) => 210 - (v / maxL) * 180;
  for (const v of [0, 200000, 400000]) { const y = gy(v); growth.append(svgEl('line', { x1: 46, x2: 446, y1: y, y2: y, stroke: RULE })); const t = svgEl('text', { x: 40, y: y + 4, 'text-anchor': 'end' }); t.textContent = v ? `${v / 1000}k` : '0'; growth.append(t); }
  for (const yr of [2004, 2008, 2012, 2016, 2020, 2024]) { const t = svgEl('text', { x: gx(yr), y: 230, 'text-anchor': 'middle' }); t.textContent = yr; growth.append(t); }
  growth.append(svgEl('path', { d: 'M' + years.map((y) => `${gx(y.yr)},${gy(y.links)}`).join('L'), fill: 'none', stroke: LINK, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  const mark = svgEl('g', {}), dot = svgEl('circle', { cx: 0, r: 5, fill: LINK, stroke: '#fff', 'stroke-width': 2 }), tag = svgEl('text', { class: 'val' });
  mark.append(svgEl('line', { x1: 0, x2: 0, y1: 22, y2: 210, stroke: INK, 'stroke-width': 1 }), dot, tag); growth.append(mark);
  const ask = debounce(async (yr) => {
    const r = await api(`/api/hubs?year=${yr}`, `The whole graph at the end of ${yr}, asked live`);
    const ms = r.sql.reduce((a, x) => a + x.ms, 0);
    live.textContent = `Asked MariaDB just now: on ${longDay(r.as_of)} the graph had ${n(r.links)} links between ${n(r.pages)} articles, and “${r.hubs[0].title}” was the most linked-to (${n(r.hubs[0].links_in)}). ${Math.round(ms)} ms.`;
  }, 350);
  slider.addEventListener('input', () => { paint(Number(slider.value)); ask(Number(slider.value)); });
  page('',
    h('h1', null, 'The whole graph, year by year'),
    h('p', { class: 'intro' }, `Every one of the ${n(s.versions)} saved versions of ${n(s.pages)} Finnish articles on the history of Finland was read for its links. MariaDB keeps each link with the day it was made and the day it was removed, so any day since ${yearOf(s.first_edit)} can be asked for.`),
    h('div', { class: 'tiles' },
      h('div', null, h('b', null, n(s.links_ever)), h('span', null, 'links ever made')),
      h('div', null, h('b', null, n(s.links_now)), h('span', null, 'links today')),
      h('div', null, h('b', null, `${Math.round((s.links_gone / s.links_ever) * 100)}%`), h('span', null, 'of all links were later removed for good')),
      h('div', null, h('b', null, n(s.lead_versions)), h('span', null, 'versions of opening paragraphs kept'))),
    h('div', { class: 'yearbar' }, yrEl, slider),
    live,
    h('div', { class: 'big' },
      h('figure', null, h('h3', null, 'The ten most linked-to articles'), h('p', null, 'How many articles of the set linked to each at the end of the year. Click a bar to see that article that year.'), hubs),
      h('figure', null, h('h3', null, 'Links in the graph'), h('p', null, 'Links alive at the end of each year.'), growth)),
    h('h2', null, 'Start with a story'),
    h('table', { class: 'list' }, h('tbody', null, d.stories.map((c) => h('tr', null,
      h('td', null, h('a', { class: 't', href: `#/a/${c.src}?e=${encodeURIComponent(c.target)}` }, c.src_title, h('span', { class: 'to' }, '›'), c.target),
        h('span', { class: 'en' }, `added ${n(c.adds)} times, removed ${n(c.removals)}, ${yearOf(c.first_at)} to ${yearOf(c.last_at)}`)))))),
    h('p', { class: 'how', style: 'margin-top:26px' }, `Built on MariaDB ${s.mariadb.split('-')[0]}. Text and data: Finnish Wikipedia and its editors, CC BY-SA 4.0, through the MariaDB Foundation’s datasets for this challenge. Rabbit Hole is not part of Wikipedia.`));
  slider.value = String(years[years.length - 1].yr);
  paint(years[years.length - 1].yr); ask(years[years.length - 1].yr);
}

async function showAsk(params) {
  const q = params.get('q') || '', year = params.get('year') || '';
  $('#q').value = q;
  page('ask-page', h('h1', { class: 'q' }, q), h('p', { class: 'loading' }, 'Searching by meaning and by words…'));
  let d;
  try { d = await api(`/api/search?q=${encodeURIComponent(q)}${year ? `&year=${year}` : ''}`, `The question: ${q}`); } catch (e) { page('ask-page', h('h1', { class: 'q' }, q), h('p', { class: 'error' }, e.message)); return; }
  const when = (y, text) => h('button', { type: 'button', 'aria-pressed': String(year === y), onclick: () => { location.hash = `#/ask?q=${encodeURIComponent(q)}${y ? `&year=${y}` : ''}`; } }, text);
  const answer = h('div', { class: 'answer', 'aria-live': 'polite' });
  page('ask-page',
    h('h1', { class: 'q' }, q),
    h('div', { class: 'when' }, 'Answer from Wikipedia as it stood', h('div', { class: 'seg' }, when('', 'today'), when('2020', 'in 2020'), when('2015', 'in 2015'), when('2010', 'in 2010'), when('2006', 'in 2006'))),
    answer,
    h('p', { class: 'how-found' }, d.mode === 'words'
      ? 'Found by words only: the embedding model is not running, so questions in another language than the article will miss. Start Ollama with bge-m3 for search by meaning.'
      : `Found by meaning and by words together${d.embed_ms != null ? ` (the question became a vector in ${d.embed_ms} ms)` : ''}.`,
      d.followed.length ? ` Then followed the links: the articles found point together at ${d.followed.map((f) => f.title).join(' and ')}.` : '',
      d.as_of ? ` The text is each article’s opening as it stood on ${longDay(d.as_of)}.` : ''),
    h('h2', null, d.sources.length ? 'Read from' : 'Nothing found'),
    h('ol', { class: 'sources' }, d.sources.map((x) => h('li', null,
      h('a', { class: 't', href: `#/a/${x.page_id}${d.as_of ? `?t=${d.as_of}` : ''}` }, x.title),
      h('p', { class: 'x' }, x.excerpt + (x.excerpt.length >= 320 ? '…' : '')),
      h('p', { class: 'm' }, h('span', null, `version of ${longDay(x.version_of)}`), h('a', { href: x.url, target: '_blank', rel: 'noopener' }, 'this version on Wikipedia'), h('a', { href: `#/a/${x.page_id}${d.as_of ? `?t=${d.as_of}` : ''}` }, 'its connections'))))),
    d.topics.some((t) => !t.in_graph) ? h('p', { class: 'how-found' }, 'Also matched, outside the graph: ' + d.topics.filter((t) => !t.in_graph).map((t) => t.title).join(', ')) : null);
  if (d.sources.length) say(answer, '/api/answer', { q, page_ids: d.sources.map((x) => x.page_id), year }, d.sources);
  else answer.textContent = 'No article in the set matches. Try other words, or the Finnish name.';
}

// ------------------------------------------------------------------ router
function route() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?'), parts = path.split('/').filter(Boolean), params = new URLSearchParams(query || '');
  const tab = parts[0] === 'a' || !parts.length ? 'explore' : parts[0] === 'ask' ? '' : parts[0];
  document.querySelectorAll('.tabs a').forEach((a) => (a.dataset.tab === tab ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  Tip.hide();
  if (tab !== 'explore') { Explore.stop(); Sql.reset(); }
  const failed = (e) => view.replaceChildren(h('p', { class: 'error' }, `Something went wrong: ${e.message}. Is MariaDB running, and has pipeline/analyze.py been run?`));
  if (tab === 'explore') {
    const id = Number(parts[1]) || START;
    if (id !== Explore.id) Sql.reset();
    Explore.open(id, params).catch(failed);
  } else if (parts[0] === 'disputes') showDisputes().catch(failed);
  else if (parts[0] === 'gaps') showGaps(parts[1]).catch(failed);
  else if (parts[0] === 'big-picture') showBigPicture().catch(failed);
  else if (parts[0] === 'ask') showAsk(params).catch(failed);
  else { location.hash = '#/'; }
}
addEventListener('hashchange', route);
addEventListener('keydown', (e) => { if (e.key === 'Escape') Sql.toggle(false); if (e.key === '/' && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); $('#q').focus(); } });
Sql.paint();
route();
