/* Tibia Loot List Manager — interface. Plain JS, no build step.
   Screens follow the "Loot Manager" Claude Design file. All interpolated text is
   HTML-escaped by the `html` template tag; only `raw()` values pass through. */
'use strict';

// ---------- plumbing ----------------------------------------------------------------

const TOKEN = (() => {
  const fromUrl = new URLSearchParams(location.search).get('t');
  if (fromUrl) { sessionStorage.setItem('t', fromUrl); history.replaceState(null, '', '/' + location.hash); }
  return fromUrl || sessionStorage.getItem('t') || '';
})();

async function api(path, params, method) {
  let url = '/api/' + path;
  const opts = { method, headers: { 'X-Token': TOKEN } };
  if (method === 'GET' && params) {
    const q = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
    if (q.length) url += '?' + new URLSearchParams(q).toString();
  } else if (method === 'POST') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(params || {});
  }
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.user = !!j.user; throw e; }
  return j;
}
const get = (p, q) => api(p, q, 'GET');
const post = (p, b) => api(p, b, 'POST');

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = s => new Raw(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const val = v => v instanceof Raw ? v.s : Array.isArray(v) ? v.map(val).join('') : (v === null || v === undefined || v === false) ? '' : esc(v);
function html(strings, ...vals) { let out = ''; strings.forEach((s, i) => { out += s; if (i < vals.length) out += val(vals[i]); }); return raw(out); }

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function fmtDate(v, withTime = true) {
  if (!v) return 'never';
  const d = typeof v === 'number' ? new Date(v * 1000)
    : /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(v + 'T00:00:00') : new Date(v);
  if (isNaN(d)) return String(v);
  const t = withTime ? `, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` : '';
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}${t}`;
}
const num = n => n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US');
const gp = n => n === null || n === undefined ? '—' : num(n) + ' gp';
const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;
const icon = (name, extra = '') => {
  let cls = 'ph ph-' + name;  // merge a class="…" passed in `extra` instead of writing the attribute twice
  extra = extra.replace(/class="([^"]*)"/, (_, c) => { cls += ' ' + c; return ''; });
  return html`<i class="${cls}" ${raw(extra)} aria-hidden="true"></i>`;
};
// Item picture from the installed client (served by /sprite/<id>); empty slot when an item has none.
const spriteSlot = (id, size, extra = '') => id
  ? html`<div class="slot has-img ${raw(extra)}" style="width:${size}px;height:${size}px"><img src="/sprite/${id}?t=${encodeURIComponent(TOKEN)}&v=${S.app ? S.app.sprite_version : ''}" alt="" loading="lazy" decoding="async"></div>`
  : html`<div class="slot ${raw(extra)}" style="width:${size}px;height:${size}px"></div>`;
const STATE_LABEL = { verified: 'Verified', unverified: 'Unverified', conflicting: 'Conflicting' };
const stateTag = (state, extra = '') => html`<span class="tag tag-sm tag-${state}" ${raw(extra)}>${STATE_LABEL[state] || state}</span>`;

// A picture that fails to load (e.g. its request was cut off by a re-render) is retried once.
document.addEventListener('error', e => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement) || !img.closest('.slot') || img.dataset.retried) return;
  img.dataset.retried = '1';
  setTimeout(() => { img.src = img.src + '&retry=1'; }, 400);
}, true);

// ---------- state -------------------------------------------------------------------

const S = {
  app: null, screen: 'catalog', modal: null, toast: null, busy: false,
  cat: { q: '', seg: 'all', cat: '', idf: 'all', rows: [], total: 0, sel: null, loadingMore: false, item: null, lookup: false },
  del: { tab: 'active', data: null },
  acc: { tab: 'active', data: null },
  exp: { sort: 'name', ids: false, data: null, copied: false },
  ins: { data: null, selected: null, mode: 'merge', editing: null },
  src: { data: null },
  help: { data: null, open: 2 },
  week: { data: null, q: '', results: [], pick: null, required: '' },
  hunt: { text: '', analysis: null, list: [] },
  onb: { step: 1, info: null, start: 'delivery', level: 'strict' },
};

const SCREENS = [
  ['catalog', 'Item catalog', 'books'], ['delivery', 'Delivery Task list', 'package'],
  ['accepted', 'My Accepted Loot', 'check-square'], ['weekly', 'Weekly Tasks', 'calendar-check'], ['hunts', 'Hunt reports', 'sword'],
  ['export', 'Copy & export', 'export'],
  ['install', 'Install to character', 'download-simple'], ['sources', 'Data sources', 'database'],
  ['help', 'Help & Support', 'lifebuoy'],
];

function toast(text, tone = 'ok') {
  S.toast = { text, tone };
  renderToast();
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { S.toast = null; renderToast(); }, 4200);
}

function showError(e, operation) {
  window.lastError = { message: e && e.message, operation, stack: e && e.stack };
  if (e && e.user) { S.modal = { kind: 'error', title: 'That didn’t work', message: e.message, operation }; }
  else { S.modal = { kind: 'error', title: 'Something went wrong', message: (e && e.message) || String(e), operation, bug: true }; }
  render();
}

async function guard(fn, operation) {
  try { return await fn(); } catch (e) { showError(e, operation); }
}

// ---------- data loading ------------------------------------------------------------

async function loadState() { S.app = await get('state'); document.getElementById('app').dataset.theme = S.app.theme; }

const PAGE = 200;
// The server pages the catalog. A fresh search loads one page; a refresh after an edit
// reloads as many rows as were already shown so the list doesn't jump back to the top.
async function loadCatalog(reset = true) {
  const c = S.cat;
  const limit = reset ? PAGE : Math.max(PAGE, c.rows.length);
  const r = await get('catalog', { q: c.q, seg: c.seg, cat: c.cat, idf: c.idf, offset: 0, limit });
  c.rows = r.rows; c.total = r.total;
  if (!c.sel && c.rows.length) c.sel = c.rows[0].key;
  if (c.sel) await loadItem(c.sel);
}
async function loadItem(key) { S.cat.sel = key; S.cat.item = await get('item', { key }); }

const LOADERS = {
  catalog: () => loadCatalog(false),
  delivery: async () => { S.del.data = await get('delivery', { tab: S.del.tab }); },
  accepted: async () => {
    const [acc, prof, levels] = await Promise.all([get('accepted', { tab: S.acc.tab }), get('profiles'), get('strictness')]);
    S.acc.data = { ...acc, profiles: prof.profiles, levels };
  },
  export: async () => { S.exp.data = await get('export', { sort: S.exp.sort, ids: S.exp.ids ? 1 : '' }); },
  install: async () => { S.ins.data = await get('install', { selected: S.ins.selected }); S.ins.selected = S.ins.data.selected; },
  sources: async () => { S.src.data = await get('sources'); },
  help: async () => { S.help.data = await get('help'); },
  weekly: async () => { S.week.data = await get('weekly'); },
  hunts: async () => { S.hunt.list = (await get('hunts')).hunts; },
};

async function go(screen) {
  S.screen = screen; S.modal = null;
  location.hash = screen;
  await guard(async () => { await LOADERS[screen](); });
  render(true);
}

async function refresh() {
  await guard(async () => { await loadState(); await LOADERS[S.screen](); });
  render();
}

// ---------- rendering ---------------------------------------------------------------

const $app = () => document.getElementById('app');

function render(resetScroll = false) {
  const main = document.querySelector('main');
  const list = document.getElementById('cat-list');
  const keep = { main: main ? main.scrollTop : 0, list: list ? list.scrollTop : 0 };
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.input : null;
  const caret = focused && document.activeElement.selectionStart;
  const app = $app();
  app.innerHTML = val(S.app && !S.app.onboarded ? viewOnboarding() : viewApp()) + val(viewModal()) + val(viewToast());
  if (!resetScroll) {
    const m = document.querySelector('main'); if (m) m.scrollTop = keep.main;
    const l = document.getElementById('cat-list'); if (l) l.scrollTop = keep.list;
  }
  if (focused) {
    const el = app.querySelector(`[data-input="${focused}"]`);
    if (el) { el.focus(); if (caret !== null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
  }
  const auto = app.querySelector('[data-autofocus]');
  if (auto && !focused) auto.focus();
}

function renderToast() {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  if (S.toast) $app().insertAdjacentHTML('beforeend', val(viewToast()));
}

function viewToast() {
  if (!S.toast) return '';
  const ic = S.toast.tone === 'ok' ? 'check-circle' : S.toast.tone === 'warn' ? 'warning' : 'info';
  return html`<div class="toast" role="status">${icon(ic, `class="${S.toast.tone}" style="font-size:17px"`)}<span>${S.toast.text}</span></div>`;
}

function viewApp() {
  const a = S.app;
  const counts = { catalog: num(a.counts.catalog), delivery: num(a.counts.delivery), accepted: num(a.counts.accepted) };
  const views = { catalog: viewCatalog, delivery: viewDelivery, accepted: viewAccepted, weekly: viewWeekly, hunts: viewHunts, export: viewExport,
    install: viewInstall, sources: viewSources, help: viewHelp };
  return html`
  <div style="flex:1;display:flex;min-height:0">
    <nav class="sidebar" aria-label="Sections">
      <div style="display:flex;align-items:center;gap:10px;padding:4px 4px 18px 10px">
        <div class="mark"></div>
        <span style="font-weight:500;font-size:15px;white-space:nowrap;flex:1">Loot List Manager</span>
        <button class="icon-btn" data-act="theme" title="Switch theme" aria-label="Switch theme">${icon(a.theme === 'dark' ? 'sun' : 'moon')}</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:2px">
        ${SCREENS.map(([k, label, ic]) => html`
          <button class="nav-btn ${S.screen === k ? 'on' : ''}" data-act="go" data-screen="${k}" ${raw(S.screen === k ? 'aria-current="page"' : '')}>
            ${icon(ic)}<span style="flex:1;min-width:0">${label}${k === 'accepted' && a.profile && a.profile.count > 1 ? html`<span class="ellipsis" style="display:block;font-size:11.5px;color:var(--muted)">${a.profile.name}</span>` : ''}</span><span class="nav-count">${counts[k] || ''}</span>
          </button>`)}
      </div>
      <div style="flex:1"></div>
      <div style="padding:12px;border-radius:8px;background:var(--color-bg);display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;align-items:center;gap:8px;font-size:12px">
          <span style="width:7px;height:7px;border-radius:50%;background:${a.any_source_error ? 'var(--warn)' : 'var(--ok)'}"></span>
          <span>${a.any_source_error ? 'Some sources failed · using cache' : 'Data cached · works offline'}</span>
        </div>
        <div style="font-size:11.5px;color:var(--muted);line-height:1.45">Last update ${fmtDate(a.last_update)}<br>Client ${a.client.version || 'not found'} · TibiaWiki</div>
        <button class="btn btn-secondary" data-act="update-start" style="font-size:13px;width:100%">${icon('arrows-clockwise')}Check for updates</button>
      </div>
    </nav>
    <main style="flex:1;min-width:0;overflow:auto;position:relative">
      ${a.notices.length ? html`<div style="margin:16px 28px 0;display:flex;gap:10px;align-items:flex-start;padding:10px 14px;border-radius:8px;background:color-mix(in srgb,var(--warn) 10%,var(--color-surface));font-size:13px">
        ${icon('info', 'class="warn" style="font-size:17px;flex:none;margin-top:1px"')}
        <div style="flex:1">${a.notices.map(n => html`<div>${n}</div>`)}</div>
        <button class="icon-btn" data-act="notices-dismiss" title="Dismiss" aria-label="Dismiss" style="width:26px;height:26px">${icon('x')}</button>
      </div>` : ''}
      ${views[S.screen]()}
    </main>
  </div>`;
}

// ---------- onboarding --------------------------------------------------------------

function viewOnboarding() {
  const o = S.onb, info = o.info || {};
  const steps = [['Welcome', 'hand-waving'], ['Tibia folder', 'folder'], ['Starting list', 'list-checks']];
  const check = (ok, text) => html`<div style="display:flex;gap:10px;align-items:center">${icon(ok ? 'check-circle' : 'warning-circle', `class="${ok ? 'ok' : 'warn'}" style="font-size:18px"`)}<span>${text}</span></div>`;
  let body;
  if (o.step === 1) {
    body = html`
      <div class="card-kicker" style="font-size:11px;margin-bottom:10px">Step 1 of 3</div>
      <h1 style="font-size:34px;margin:0 0 14px">Build your Accepted Loot list</h1>
      <p style="font-size:15px;color:var(--muted);line-height:1.6;max-width:540px">This app starts you with every item a Delivery Task can ask for. Add or remove anything from the catalog, then copy the list for the Cyclopedia or install it into a character’s loot file.</p>
      <div style="display:grid;gap:14px;margin-top:22px">
        ${[['cpu', 'Never touches the game client', 'It reads files on disk. It does not read memory or automate anything.'],
           ['key', 'No account details', 'You will never be asked to sign in to Tibia.'],
           ['floppy-disk', 'Backups before every write', 'Installing always shows a preview and saves a timestamped copy first.']]
          .map(([ic, t, d]) => html`<div style="display:flex;gap:14px;align-items:flex-start">${icon(ic, 'class="accent" style="font-size:20px;margin-top:2px"')}<div><div style="font-weight:500">${t}</div><div style="font-size:13px;color:var(--muted)">${d}</div></div></div>`)}
      </div>`;
  } else if (o.step === 2) {
    body = html`
      <div class="card-kicker" style="font-size:11px;margin-bottom:10px">Step 2 of 3</div>
      <h1 style="font-size:30px;margin:0 0 10px">Find your Tibia folder</h1>
      <p style="font-size:14px;color:var(--muted);max-width:540px">Item IDs come from your installed client, so the catalog always matches the game you play. You can skip this and use the manual list only.</p>
      <div class="field" style="margin-top:16px">
        <label for="onb-folder">Tibia characterdata folder</label>
        <div style="display:flex;gap:8px">
          <input id="onb-folder" class="input mono" readonly value="${info.folder || ''}" style="font-size:12.5px">
          <button class="btn btn-secondary" data-act="folder-browse" style="flex:none">Browse…</button>
        </div>
      </div>
      <div style="margin-top:18px;display:grid;gap:10px;padding:14px 16px;border-radius:8px;background:var(--color-surface)">
        ${check(info.client_found, info.client_found ? `Client ${info.client_version} found · ${num(info.items)} lootable items` : 'Installed client not found in this folder’s Tibia package')}
        ${check(info.characters > 0, info.characters > 0 ? `${plural(info.characters, 'character folder', 'character folders')} found` : 'No character folders found')}
        ${check(info.format_ok, info.format_ok ? 'Existing loot files match the known format · installing can be enabled' : info.format_message || '')}
      </div>
      <p style="font-size:12.5px;color:var(--muted);margin-top:12px">Started from the commonly reported location. If it is wrong, choose the folder yourself.</p>`;
  } else {
    const opt = (key, title, desc, tag, extra = '') => html`
      <div class="choice ${o.start === key ? 'on' : ''}" data-act="onb-start" data-v="${key}" role="radio" aria-checked="${o.start === key}" tabindex="0" style="padding:16px">
        <i class="${o.start === key ? 'ph-fill ph-radio-button' : 'ph ph-circle'} pick" style="font-size:20px"></i>
        <div style="flex:1"><div style="font-weight:500">${title} ${tag ? html`<span class="tag tag-accent" style="margin-left:6px">${tag}</span>` : ''}</div><div style="font-size:13px;color:var(--muted);margin-top:4px">${desc}</div>${extra}</div>
      </div>`;
    body = html`
      <div class="card-kicker" style="font-size:11px;margin-bottom:10px">Step 3 of 3</div>
      <h1 style="font-size:30px;margin:0 0 10px">Choose a starting list</h1>
      <p style="font-size:14px;color:var(--muted);max-width:540px">You can change this at any time. Your edits are kept separately from the source list.</p>
      <div style="display:grid;gap:10px;margin-top:16px">
        ${opt('delivery', 'All Delivery Task items', `${num(info.delivery_items)} items from TibiaWiki’s Delivery Task page, revised ${fmtDate(info.delivery_revised, false)}.` + (info.delivery_blocked ? ` ${info.delivery_blocked} cannot be matched to a client ID yet and will be left out of exports.` : ' All of them match a verified client ID.'), 'Recommended')}
        ${info.levels && info.levels.length ? opt('level', 'A strictness level, plus Delivery Task items', 'A ready-made list by item value (the highest NPC price, or Market category when no NPC buys it). You can change the level later on My Accepted Loot.', '',
          o.start === 'level' ? html`<select class="input" data-change="onb-level" style="margin-top:10px;width:auto;min-height:32px;padding:4px 8px" aria-label="Strictness level">${info.levels.map(l => html`<option value="${l.id}" ${raw(o.level === l.id ? 'selected' : '')}>${l.name} · ${num(l.count)} items</option>`)}</select>` : '') : ''}
        ${opt('empty', 'Start empty', 'Add items one by one from the catalog.')}
      </div>`;
  }
  return html`
  <div style="flex:1;display:flex;min-height:0">
    <div style="width:260px;flex:none;background:var(--panel);padding:36px 24px;display:flex;flex-direction:column;gap:22px">
      <div style="display:flex;align-items:center;gap:10px"><div class="mark"></div><span style="font-weight:500;font-size:15px;white-space:nowrap">Loot List Manager</span></div>
      <div style="display:flex;flex-direction:column;gap:4px;margin-top:12px">
        ${steps.map(([label, ic], i) => {
          const n = i + 1, done = n < o.step, cur = n === o.step;
          return html`<div style="display:flex;align-items:center;gap:12px;padding:9px 10px;border-radius:8px;background:${cur ? 'color-mix(in srgb,var(--color-accent) 16%,transparent)' : 'transparent'};color:${cur ? 'var(--color-accent-300)' : done ? 'var(--color-text)' : 'var(--muted)'}">
            <div style="width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:12px;border:1px solid ${cur || done ? 'var(--color-accent)' : 'var(--color-divider)'}"><i class="${done ? 'ph-bold ph-check' : 'ph ph-' + ic}"></i></div>
            <span style="font-size:14px">${label}</span></div>`;
        })}
      </div>
      <div style="flex:1"></div>
      <div style="font-size:12px;color:var(--muted);line-height:1.5">Local helper. No game memory access, no account details, nothing uploaded.</div>
    </div>
    <div style="flex:1;overflow:auto;padding:56px 64px;display:flex;flex-direction:column">
      <div style="max-width:620px;flex:1;display:flex;flex-direction:column">
        ${body}
        <div style="flex:1;min-height:32px"></div>
        <div style="display:flex;gap:8px;align-items:center">
          ${o.step > 1 ? html`<button class="btn btn-secondary" data-act="onb-back">Back</button>` : ''}
          <div style="flex:1"></div>
          ${o.step === 2 ? html`<button class="btn btn-ghost" data-act="onb-next" style="padding-inline:10px">Skip, manual list only</button>` : ''}
          <button class="btn btn-primary" data-act="onb-next" style="padding:8px 18px" data-autofocus>${o.step === 3 ? 'Open my list' : 'Continue'}</button>
        </div>
      </div>
    </div>
  </div>`;
}

// ---------- catalog -----------------------------------------------------------------

function viewCatalog() {
  const c = S.cat, a = S.app;
  return html`
  <div style="display:flex;height:100%;min-height:0">
    <div style="flex:1;min-width:0;display:flex;flex-direction:column;padding:24px 28px 0;overflow:hidden">
      <h1 style="font-size:24px;margin:0 0 4px">Item catalog</h1>
      <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;font-size:13px;color:var(--muted);margin-bottom:16px">
        <span>${num(a.counts.catalog)} lootable items from your installed client ${a.client.version || ''}</span>
        ${a.reference_ok
          ? html`<span style="display:flex;gap:6px;align-items:center">${icon('seal-check', 'class="ok"')}Reference check passed: gold coin = <span class="mono" style="color:var(--color-text)">3031</span>, platinum coin = <span class="mono" style="color:var(--color-text)">3035</span></span>`
          : html`<span style="display:flex;gap:6px;align-items:center" class="bad">${icon('warning-octagon')}Reference check failed — exports blocked</span>`}
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <div style="position:relative;flex:1;min-width:220px;max-width:380px">
          ${icon('magnifying-glass', 'style="position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted)"')}
          <input class="input" data-input="search" placeholder="Search items by name or ID" value="${c.q}" style="padding-left:32px" aria-label="Search items">
        </div>
        <div class="seg" role="radiogroup" aria-label="Which items">
          ${[['all', 'All items'], ['del', 'Delivery Task'], ['mine', 'In my list'], ['fav', 'Favorites']].map(([k, l]) => html`
            <label class="seg-opt nowrap"><input type="radio" name="catseg" data-act="cat-seg" data-v="${k}" ${raw(c.seg === k ? 'checked' : '')}>${l}</label>`)}
        </div>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:12px 0 10px">
        ${[['all', 'All IDs'], ['verified', 'Verified'], ['unverified', 'Unverified'], ['conflicting', 'Conflicting']].map(([k, l]) => html`
          <button class="chip ${c.idf === k ? 'on' : ''}" data-act="cat-idf" data-v="${k}">${l}</button>`)}
        <span style="width:1px;height:18px;background:var(--color-divider);margin:0 6px"></span>
        <select class="input" data-change="cat-cat" aria-label="Category" style="width:auto;min-height:28px;padding:3px 8px;font-size:12px">
          <option value="">All categories</option>
          ${a.categories.map(k => html`<option value="${k}" ${raw(c.cat === k ? 'selected' : '')}>${k}</option>`)}
        </select>
        <span id="cat-count" style="margin-left:auto;font-size:12px;color:var(--muted)">${catCount(c)}</span>
      </div>
      ${viewCatalogTools()}
      <div class="cat-grid rule-strong" style="padding:8px 10px" role="row">
        <span></span><span class="kicker">Item · notes</span><span class="kicker">Item ID</span><span class="kicker">Lists</span><span></span>
      </div>
      <div id="cat-list" style="flex:1;overflow:auto;padding-bottom:20px">${viewCatalogRows()}</div>
    </div>
    ${viewDetail()}
  </div>`;
}

const catFiltered = c => !!(c.q.trim() || c.seg !== 'all' || c.cat || c.idf !== 'all');
const catCount = c => `${c.rows.length < c.total ? `${num(c.rows.length)} of ${num(c.total)}` : num(c.total)} items`;
const searchOf = c => ({ q: c.q, seg: c.seg, cat: c.cat, idf: c.idf });

function viewCatalogTools() {
  const c = S.cat, saved = S.app.saved_searches || [];
  const isCurrent = s => s.q === c.q.trim() && s.seg === c.seg && s.cat === c.cat && s.idf === c.idf;
  const filtered = catFiltered(c);
  if (!saved.length && !filtered) return html`<div id="cat-tools"></div>`;
  return html`<div id="cat-tools" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:-2px 0 10px">
    ${saved.map((s, i) => html`<span class="chip saved-chip ${isCurrent(s) ? 'on' : ''}">
      <button class="saved-apply" data-act="search-apply" data-v="${i}" title="Apply this saved search">${icon('bookmark-simple')}${s.name}</button>
      <button class="saved-x" data-act="search-delete" data-v="${s.name}" title="Delete saved search" aria-label="Delete saved search ${s.name}">${icon('x')}</button></span>`)}
    ${filtered && !saved.some(isCurrent) ? html`<button class="chip" data-act="search-save-open" style="display:inline-flex;align-items:center;gap:5px">${icon('plus')}Save this search</button>` : ''}
    ${filtered && c.total ? html`<span style="margin-left:auto;display:flex;gap:4px">
      <button class="btn btn-ghost" data-act="bulk" data-v="add" style="font-size:12.5px;padding:3px 8px;min-height:28px">${icon('list-plus')}Add all to Accepted Loot</button>
      <button class="btn btn-ghost" data-act="bulk" data-v="remove" style="font-size:12.5px;padding:3px 8px;min-height:28px">${icon('minus-circle')}Remove all from Accepted Loot</button></span>` : ''}
  </div>`;
}

function viewCatalogRows() {
  const c = S.cat;
  if (!c.rows.length) return html`<div style="padding:30px 10px;color:var(--muted)">${c.q ? html`No items match “${c.q}”.` : 'No items match these filters.'}</div>`;
  return html`${c.rows.map(r => html`
    <div class="cat-grid cat-row ${c.sel === r.key ? 'sel' : ''}" data-act="cat-select" data-key="${r.key}">
      ${spriteSlot(r.id, 34)}
      <div style="min-width:0"><div class="ellipsis" style="font-size:14px">${r.favorite ? html`<i class="ph-fill ph-star fav-star" title="Favorite" aria-label="Favorite"></i> ` : ''}${r.name}</div><div class="ellipsis" style="font-size:12px;color:var(--muted)">${r.sub}</div></div>
      <div class="mono" style="font-size:12.5px;color:${r.id ? 'var(--color-text)' : 'var(--warn)'}">${r.id ?? '—'}</div>
      <div style="display:flex;gap:5px;flex-wrap:wrap">
        ${r.in_delivery ? html`<span class="tag tag-accent tag-sm">Delivery</span>` : ''}
        ${r.state !== 'verified' ? stateTag(r.state) : ''}
      </div>
      <button class="toggle-btn ${r.in_accepted ? 'on' : ''}" data-act="cat-toggle" data-key="${r.key}" title="${r.in_accepted ? 'Remove from Accepted Loot' : 'Add to Accepted Loot'}" aria-label="${r.in_accepted ? 'Remove ' + r.name + ' from Accepted Loot' : 'Add ' + r.name + ' to Accepted Loot'}">
        <i class="${r.in_accepted ? 'ph-bold ph-check' : 'ph ph-plus'}"></i></button>
    </div>`)}
    ${c.total > c.rows.length ? html`<div style="padding:14px 10px"><button class="btn btn-secondary" data-act="cat-more" ${raw(c.loadingMore ? 'disabled' : '')}>${c.loadingMore ? 'Loading…' : `Show ${num(Math.min(PAGE, c.total - c.rows.length))} more (${num(c.total - c.rows.length)} left)`}</button></div>` : ''}`;
}

function valueRows(rows, empty) {
  if (!rows.length) return html`<span style="color:var(--muted)">${empty}</span>`;
  return html`${rows.map(v => html`<div style="display:flex;flex-direction:column;gap:1px">
    <div style="display:flex;justify-content:space-between;gap:10px"><span class="mono">${num(v.amount)} ${v.currency === 'gold' ? 'gp' : v.currency}</span>
      <span style="font-size:11.5px;color:var(--muted);text-align:right">${v.npcs.length ? v.npcs.slice(0, 2).join(', ') + (v.npcs.length > 2 ? ` +${v.npcs.length - 2}` : '') : ''}</span></div>
    <div style="font-size:11.5px;color:var(--muted)">${v.source}${v.authority ? ' · ' + v.authority : ''} · ${fmtDate(v.retrieved, false)}${v.stale ? html` <span class="warn">· may be outdated</span>` : ''}</div>
  </div>`)}`;
}

function viewDetail() {
  const d = S.cat.item;
  if (!d) return html`<aside class="detail"><div style="color:var(--muted)">Select an item to see its details.</div></aside>`;
  return html`
  <aside class="detail" aria-label="Item details">
    <div style="display:flex;gap:14px;align-items:center">
      ${spriteSlot(d.id, 64, 'lg')}
      <div style="min-width:0;flex:1"><div style="font-size:20px;font-weight:500;line-height:1.2">${d.name}</div><div style="font-size:12.5px;color:var(--muted);margin-top:3px">${d.category}</div></div>
      <button class="icon-btn fav-btn ${d.favorite ? 'on' : ''}" data-act="detail-fav" title="${d.favorite ? 'Remove from favorites' : 'Add to favorites'}" aria-label="${d.favorite ? 'Remove from favorites' : 'Add to favorites'}" aria-pressed="${d.favorite}">
        <i class="${d.favorite ? 'ph-fill' : 'ph'} ph-star"></i></button>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      ${d.in_accepted
        ? html`<button class="btn btn-secondary" data-act="detail-acc" style="justify-content:flex-start">${icon('minus-circle')}Remove from my Accepted Loot</button>`
        : html`<button class="btn btn-primary" data-act="detail-acc" style="justify-content:flex-start">${icon('plus-circle')}Add to my Accepted Loot</button>`}
      <button class="btn btn-ghost" data-act="detail-del" style="justify-content:flex-start;padding-inline:10px">${icon('package')}${d.in_delivery ? 'Exclude from Delivery Task list' : 'Add to Delivery Task list'}</button>
    </div>
    <div class="kv">
      <span class="k">Tibia item ID</span>
      <span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="mono">${d.id ?? '—'}</span>${stateTag(d.state)}</span>
      <span></span><span style="font-size:12px;color:var(--muted);margin-top:-4px">${d.conflicting_pages.length ? d.reason.replace(/\.$/, '') + ' (' + d.conflicting_pages.join(', ') + ').' : d.reason}${d.state !== 'verified' ? ' Not exported until verified.' : ''}</span>
      ${d.note ? html`<span class="k">Notes</span><span>${d.note}</span>` : ''}
      ${d.same_name.length ? html`<span class="k">Same name</span><span>${d.same_name.map(s => s.id + (s.wiki ? ` (${s.wiki})` : '')).join(', ')}</span>` : ''}
      ${d.wiki ? html`<span class="k">TibiaWiki</span><span>${d.wiki.title}${d.wiki.pageid ? html` <span style="color:var(--muted);font-size:11.5px">· page ${d.wiki.pageid}, not an item ID</span>` : ''}</span>` : ''}
      ${d.tier ? html`<span class="k">Loot tier</span><span style="display:flex;flex-direction:column;gap:2px">
        <span><span class="tag tag-sm tier tier-${d.tier.tier}">${d.tier.tier === 'junk' ? 'Junk' : 'Tier ' + d.tier.tier}</span></span>
        <span style="font-size:12px;color:var(--muted)">${d.tier.reason}. ${d.tier.levels.length ? `Included from ${d.tier.levels[d.tier.levels.length - 1]} down to Soft.` : 'Not in any strictness level.'}</span></span>` : ''}
      <span class="k">Delivery Task</span><span>${d.delivery_text}</span>
      <span class="k">Requested</span><span>${d.qty ? d.qty + ' per task' : '—'}</span>
    </div>
    <div>
      <div class="kicker" style="margin-bottom:8px">Values</div>
      <div style="display:grid;gap:10px;font-size:13px">
        <div style="display:grid;gap:4px"><span>NPCs pay you</span>${valueRows(d.pays, 'No NPC buys it')}</div>
        <div style="display:grid;gap:4px"><span>NPCs charge</span>${valueRows(d.charges, 'Not sold by NPCs')}</div>
        <div style="display:flex;justify-content:space-between;gap:10px;padding-top:4px"><span>Market</span><span style="color:var(--muted)">No source configured</span></div>
        <div style="font-size:11.5px;color:var(--muted)">Market prices vary by world. None are shown until you pick a source.</div>
      </div>
    </div>
    <div>
      <div class="kicker" style="margin-bottom:8px">Dropped by</div>
      ${d.drops && !d.drops.not_found
        ? html`<div style="display:flex;gap:5px;flex-wrap:wrap">${(d.drops.list.length ? d.drops.list.slice(0, 12).map(x => html`<span class="tag tag-neutral">${x}</span>`) : html`<span style="font-size:13px;color:var(--muted)">TibiaWiki lists no creatures for this item.</span>`)}${d.drops.list.length > 12 ? html`<span class="tag tag-outline">+${d.drops.list.length - 12} more</span>` : ''}</div>
               <div style="font-size:11.5px;color:var(--muted);margin-top:8px">TibiaWiki · page revised ${fmtDate(d.drops.revised, false)} · fetched ${fmtDate(d.drops.fetched, false)}</div>`
        : d.drops && d.drops.not_found
          ? html`<div style="font-size:13px;color:var(--muted)">No TibiaWiki page lists this item ID.</div>`
          : html`<div style="font-size:13px;color:var(--muted);margin-bottom:8px">Not loaded yet.</div>
                 <button class="btn btn-secondary" data-act="detail-lookup" ${raw(S.cat.lookup ? 'disabled' : '')} style="font-size:13px">${S.cat.lookup ? html`<i class="ph ph-circle-notch spin"></i>Looking up…` : html`${icon('globe-simple')}Look up on TibiaWiki`}</button>`}
    </div>
    ${d.drops && d.drops.url ? html`<a href="#" data-act="open-url" data-url="${d.drops.url}" style="font-size:12.5px;display:flex;align-items:center;gap:6px;text-decoration:none">${icon('arrow-square-out')}${d.drops.url.replace('https://', '')}</a>` : ''}
    <button class="btn btn-ghost" data-act="report-open" data-category="item_id" data-key="${d.key}" style="justify-content:flex-start;padding-inline:10px;font-size:13px;color:var(--color-text)">${icon('flag')}Report incorrect item ID…</button>
  </aside>`;
}

// ---------- delivery ----------------------------------------------------------------

const TONE = {
  neutral: 'background:var(--color-neutral-800);color:var(--muted)', accent: 'background:var(--color-accent-800);color:var(--color-accent-100)',
  plain: 'background:transparent;color:var(--muted)', unverified: 'background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)',
  conflicting: 'background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)',
};

function viewDelivery() {
  const d = S.del.data;
  if (!d) return '';
  return html`
  <div class="page" style="max-width:1080px">
    <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap">
      <div style="flex:1;min-width:280px"><h1>Delivery Task list</h1><div class="page-sub">Items a Delivery Task can request. Source list with your edits applied.</div>
        <div class="scope-note">${icon('users-three')}Shared by all profiles: edits here change the Delivery Task list for every profile.</div></div>
      <button class="btn btn-secondary" data-act="del-defaults">${icon('arrow-counter-clockwise')}Restore source defaults</button>
    </div>
    <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:18px 0 14px;font-size:12.5px;color:var(--muted)">
      <span style="display:flex;gap:6px;align-items:center">${icon('globe-simple')}TibiaWiki Fandom · Delivery Task${d.source.bundled ? ' (bundled copy)' : ''}</span>
      <span>Page revised ${fmtDate(d.source.revised)}</span><span>Fetched ${fmtDate(d.source.fetched, false)}</span>
      <a href="#" data-act="open-url" data-url="${d.source.url}" style="text-decoration:none">Open source page</a>
    </div>
    <div class="seg" style="margin-bottom:16px" role="radiogroup" aria-label="Show">
      ${[['active', 'Active'], ['added', 'Added by me'], ['excluded', 'Excluded by me']].map(([k, l]) => html`
        <label class="seg-opt nowrap"><input type="radio" name="deltab" data-act="del-tab" data-v="${k}" ${raw(S.del.tab === k ? 'checked' : '')}>${l}<span class="mono" style="font-size:11.5px;color:var(--muted)">${num(d.counts[k])}</span></label>`)}
    </div>
    ${d.groups.map(g => html`
      <div style="margin-bottom:18px">
        <div style="display:flex;align-items:baseline;gap:10px;padding:6px 8px"><span style="font-size:15px;font-weight:500">${g.category}</span><span style="font-size:12px;color:var(--muted)">${plural(g.rows.length, 'item', 'items')}</span></div>
        <table class="table">
          <thead><tr><th style="width:44px"></th><th>Item</th><th style="width:90px">Client ID</th><th style="width:100px">Requested</th><th style="width:120px;text-align:right">NPCs pay</th><th style="width:150px">Status</th><th style="width:130px"></th></tr></thead>
          <tbody>${g.rows.map(r => html`
            <tr>
              <td>${spriteSlot(r.id, 28)}</td>
              <td><a href="#" data-act="show-item" data-key="${r.key}" style="color:${r.on ? 'var(--color-text)' : 'var(--muted)'};text-decoration:none">${r.name}</a></td>
              <td class="mono" style="font-size:12.5px;color:${r.id ? 'var(--color-text)' : 'var(--warn)'}">${r.id ?? '—'}</td>
              <td class="mono" style="font-size:12.5px">${r.qty}</td>
              <td class="mono" style="font-size:12.5px;text-align:right">${gp(r.pays)}</td>
              <td><span class="tag tag-sm" style="${raw(TONE[r.tone] || TONE.plain)}">${r.status}</span></td>
              <td style="text-align:right"><button class="btn btn-ghost" data-act="del-toggle" data-key="${r.key}" style="font-size:13px;padding-inline:8px">${r.action}</button></td>
            </tr>`)}</tbody>
        </table>
      </div>`)}
    ${!d.groups.length ? html`<div style="padding:24px 8px;color:var(--muted)">${S.del.tab === 'added' ? 'You haven’t added any items. Use “Add to Delivery Task list” in the catalog.' : S.del.tab === 'excluded' ? 'Nothing excluded. Every source item is on the list.' : 'The list is empty.'}</div>` : ''}
    <div style="font-size:12.5px;color:var(--muted);margin-top:4px">Add any other item from the catalog with “Add to Delivery Task list”.</div>
  </div>`;
}

// ---------- accepted ----------------------------------------------------------------

function viewAccepted() {
  const d = S.acc.data;
  if (!d) return '';
  const c = d.counts;
  return html`
  <div class="page" style="max-width:1080px">
    <div style="display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap">
      <div style="flex:1"><h1>My Accepted Loot</h1><div class="page-sub">The list you will copy or install.</div></div>
      <button class="btn btn-secondary" data-act="acc-defaults">${icon('arrow-counter-clockwise')}Restore defaults</button>
    </div>
    <div class="profile-bar">
      ${icon('user-list', 'class="accent" style="font-size:18px"')}
      <label class="muted" for="profile-select" style="font-size:13px">Profile</label>
      <select id="profile-select" class="input" data-change="profile-switch" style="width:auto;min-width:180px;min-height:32px;padding:4px 8px">
        ${(d.profiles || []).map(p => html`<option value="${p.id}" ${raw(p.active ? 'selected' : '')}>${p.name}</option>`)}
      </select>
      <button class="btn btn-secondary" data-act="profile-new" style="font-size:13px">${icon('plus')}New…</button>
      <button class="btn btn-secondary" data-act="profiles-open" style="font-size:13px">${icon('gear-six')}Manage…</button>
      <button class="btn btn-ghost" data-act="history-open" style="font-size:13px;padding-inline:8px">${icon('clock-counter-clockwise')}History…</button>
    </div>
    ${viewLevels(d)}
    <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:22px 0 6px">
      <span style="font-size:44px;font-weight:500;letter-spacing:-.02em;line-height:1">${num(c.total)}</span><span style="font-size:15px;color:var(--muted)">items</span>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:13.5px;color:var(--muted)">
      <span class="mono" style="color:var(--color-text)">${num(c.from_delivery)}</span><span>from Delivery Task list</span>
      ${d.level.id ? html`<span>+</span><span class="mono" style="color:var(--color-text)">${num(c.from_level)}</span><span>from the ${d.level.name} level</span>` : ''}
      <span>+</span><span class="mono" style="color:var(--color-text)">${num(c.added)}</span><span>added by me</span>
      <span>−</span><span class="mono" style="color:var(--color-text)">${num(c.removed)}</span><span>removed by me</span>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin:20px 0 22px">
      ${c.blocked ? html`<div class="note warn-bg" style="flex:1;min-width:280px">${icon('warning', 'class="warn"')}
        <div><div style="font-weight:500;margin-bottom:3px">${plural(c.blocked, 'item can’t', 'items can’t')} be exported yet</div><div style="color:var(--muted)">Their Tibia item IDs are unverified or conflicting. They stay on your list and appear in the manual checklist, but are left out of game files.</div></div></div>`
      : !c.total ? html`<div class="note" style="flex:1;min-width:280px">${icon('tray', 'class="muted"')}<div><div style="font-weight:500;margin-bottom:3px">This list is empty</div><div style="color:var(--muted)">Add items from the catalog, or include the Delivery Task list below.</div></div></div>`
      : html`<div class="note" style="flex:1;min-width:280px">${icon('seal-check', 'class="ok"')}<div><div style="font-weight:500;margin-bottom:3px">All ${num(c.total)} items can be exported</div><div style="color:var(--muted)">Every item has a verified Tibia item ID.</div></div></div>`}
      <div class="note ${d.limit.warn ? 'warn-bg' : ''}" style="flex:1;min-width:280px">${icon('gauge', `class="${d.limit.warn ? 'warn' : 'muted'}"`)}
        <div><div style="font-weight:500;margin-bottom:3px">${d.limit.title}</div><div style="color:var(--muted)">${d.limit.text} ${S.app.limit === null ? html`<a href="#" data-act="go" data-screen="sources">Set one in Data sources</a>` : ''}</div></div></div>
    </div>
    <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
      <label style="display:flex;align-items:center;gap:10px;font-size:13.5px;cursor:pointer" data-act="acc-follow" role="switch" aria-checked="${d.follow_delivery}" tabindex="0">
        <span class="switch ${d.follow_delivery ? 'on' : ''}"><span></span></span>${d.level.id ? 'Always include Delivery Task items' : 'Include the Delivery Task list'}</label>
      <div class="seg" style="margin-left:auto" role="radiogroup" aria-label="Show">
        ${[['active', 'On my list', c.total], ['added', 'Added by me', c.added], ['removed', 'Removed by me', c.removed]].map(([k, l, n]) => html`
          <label class="seg-opt nowrap"><input type="radio" name="acctab" data-act="acc-tab" data-v="${k}" ${raw(S.acc.tab === k ? 'checked' : '')}>${l}<span class="mono" style="font-size:11.5px;color:var(--muted)">${num(n)}</span></label>`)}
      </div>
    </div>
    <table class="table">
      <thead><tr><th style="width:44px"></th><th>Item</th><th style="width:100px">Item ID</th><th style="width:170px">Comes from</th><th style="width:110px"></th></tr></thead>
      <tbody>${d.rows.map(r => html`
        <tr>
          <td>${spriteSlot(r.id, 28)}</td>
          <td><a href="#" data-act="show-item" data-key="${r.key}" style="color:inherit;text-decoration:none">${r.name}</a> ${r.state !== 'verified' ? stateTag(r.state, 'style="margin-left:6px;padding:1px 7px"') : ''}</td>
          <td class="mono" style="font-size:12.5px;color:${r.id ? 'var(--color-text)' : 'var(--warn)'}">${r.id ?? '—'}</td>
          <td style="font-size:13px;color:var(--muted)">${r.from}</td>
          <td style="text-align:right"><button class="btn btn-ghost" data-act="acc-toggle" data-key="${r.key}" style="font-size:13px;padding-inline:8px">${r.on ? 'Remove' : 'Put back'}</button></td>
        </tr>`)}</tbody>
    </table>
    ${!d.rows.length ? html`<div style="padding:24px 8px;color:var(--muted)">Nothing here.</div>` : ''}
  </div>`;
}

function viewLevels(d) {
  const L = d.levels;
  if (!L) return '';
  const opt = (id, name, count, sub) => html`<button class="level ${L.current === id ? 'on' : ''}" data-act="level-pick" data-v="${id}" aria-pressed="${L.current === id}">
    <span class="level-name">${name}</span><span class="level-count mono">${count === null ? sub : num(count)}</span></button>`;
  return html`<div class="level-panel">
    <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
      <h6 style="margin:0">Strictness level</h6>
      <span style="font-size:12.5px;color:var(--muted)">Ready-made lists, softest to strictest. Your changes override the level.</span>
    </div>
    <div style="font-size:12px;color:var(--muted);margin-top:-4px">Based on the highest price an NPC pays in your installed client, or the item’s Market category when no NPC buys it. Not Market prices, your world or your hunting habits. Each item’s tier is shown in the catalog.</div>
    <div class="level-track" role="group" aria-label="Strictness level">
      ${opt('', 'None', null, 'off')}
      ${L.levels.map(l => opt(l.id, l.name, l.count))}
    </div>
    ${d.level.pending ? html`<div class="note warn-bg" style="margin-top:4px">${icon('arrows-clockwise', 'class="warn"')}
      <div style="flex:1"><div style="font-weight:500;margin-bottom:3px">New prices change the ${d.level.name} level</div>
        <div class="muted">${d.level.pending.add.length || d.level.pending.remove.length ? `${num(d.level.pending.add.length)} to add, ${num(d.level.pending.remove.length)} to remove. Nothing changes until you accept.` : 'Your list stays the same: the items involved are already on it, or kept off it by you.'}</div></div>
      <button class="btn btn-primary" data-act="level-review" style="flex:none;align-self:center">Review…</button></div>` : ''}
  </div>`;
}

// ---------- weekly tasks ------------------------------------------------------------

function viewWeeklyResults() {
  const w = S.week;
  if (!w.q.trim()) return '';
  if (!w.results.length) return html`<div class="muted" style="padding:8px 4px;font-size:13px">No Delivery Task item matches “${w.q}”.</div>`;
  return html`<div class="week-results">${w.results.map(r => html`
    <button class="week-result ${w.pick && w.pick.key === r.key ? 'on' : ''}" data-act="week-pick" data-key="${r.key}" ${raw(r.tracked ? 'disabled' : '')}>
      ${spriteSlot(r.id, 28)}<span style="flex:1;text-align:left">${r.name}<span style="display:block;font-size:12px;color:var(--muted)">${r.category || ''} · asks for ${r.min ?? '?'}–${r.max ?? '?'}</span></span>
      ${r.tracked ? html`<span class="tag tag-neutral tag-sm">Tracked</span>` : ''}
    </button>`)}</div>`;
}

function viewWeekly() {
  const d = S.week.data, w = S.week;
  if (!d) return '';
  const done = d.tasks.filter(t => t.done).length;
  const left = d.tasks.reduce((n, t) => n + t.remaining, 0);
  const missing = d.tasks.filter(t => !t.in_accepted && t.exportable);
  const reset = new Date(d.next_reset), days = Math.max(0, Math.ceil((reset - Date.now()) / 86400000));
  return html`
  <div class="page" style="max-width:1080px">
    <h1>Weekly Tasks</h1>
    <div class="page-sub">Week of ${fmtDate(d.week_start, false)} · resets at Monday’s server save, 10:00 German time: ${fmtDate(d.next_reset)} your time (${days === 0 ? 'today' : plural(days, 'day', 'days')})</div>
    ${d.new_week ? html`<div class="note" style="margin-top:14px">${icon('calendar-check', 'class="accent"')}<div>A new week started at Monday’s server save. Last week’s tasks were moved to <b>Previous weeks</b> below.</div></div>` : ''}
    <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:22px 0 6px">
      <span style="font-size:44px;font-weight:500;letter-spacing:-.02em;line-height:1">${num(done)}<span class="muted" style="font-size:28px"> / ${num(d.tasks.length)}</span></span>
      <span style="font-size:15px;color:var(--muted)">tasks done${d.tasks.length ? ` · ${plural(left, 'item', 'items')} still to collect` : ''}</span>
    </div>
    ${missing.length ? html`<div class="note warn-bg" style="margin:14px 0">${icon('warning', 'class="warn"')}
      <div style="flex:1"><div style="font-weight:500;margin-bottom:3px">${plural(missing.length, 'task item isn’t', 'task items aren’t')} on your Accepted Loot list</div>
        <div class="muted">Profile “${d.profile || ''}” won’t loot ${missing.map(t => t.name).join(', ')}.</div></div>
      <button class="btn btn-primary" data-act="week-add-missing" style="flex:none;align-self:center">Add ${missing.length === 1 ? 'it' : 'them'}</button></div>` : ''}
    <div class="panel" style="margin:18px 0;gap:10px">
      <h5 style="margin:0">Add this week’s task</h5>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <div style="position:relative;flex:1;min-width:240px">
          ${icon('magnifying-glass', 'style="position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted)"')}
          <input class="input" data-input="weekly-search" value="${w.q}" placeholder="Find a Delivery Task item" style="padding-left:32px" aria-label="Find a Delivery Task item">
        </div>
        <label style="display:flex;gap:6px;align-items:center;font-size:13px">Required
          <input class="input mono" data-input="weekly-required" inputmode="numeric" value="${w.required}" placeholder="${w.pick ? w.pick.min : ''}" style="width:90px"></label>
        <button class="btn btn-primary" data-act="week-add" ${raw(w.pick ? '' : 'disabled')}>${icon('plus')}${w.pick ? 'Add ' + w.pick.name : 'Add task'}</button>
      </div>
      <div id="week-results">${viewWeeklyResults()}</div>
    </div>
    <div class="week-grid">${d.tasks.map(t => html`
      <div class="task-card ${t.done ? 'done' : ''}">
        <div style="display:flex;gap:12px;align-items:center">
          ${spriteSlot(t.id, 40)}
          <div style="flex:1;min-width:0"><div class="ellipsis" style="font-weight:500">${t.name}</div><div style="font-size:12px;color:var(--muted)">${t.category || ''} · asks for ${t.min ?? '?'}–${t.max ?? '?'}</div></div>
          ${t.done ? html`<span class="tag tag-verified tag-sm">${icon('check')} Done</span>` : ''}
          <button class="icon-btn" data-act="week-remove" data-key="${t.key}" title="Remove task" aria-label="Remove ${t.name}">${icon('trash')}</button>
        </div>
        <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${t.required}" aria-valuenow="${Math.min(t.collected, t.required)}"><span style="width:${Math.min(100, Math.round(100 * t.collected / Math.max(1, t.required)))}%"></span></div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <input class="input mono task-num" data-change="week-collected" data-key="${t.key}" value="${t.collected}" inputmode="numeric" aria-label="Collected">
          <span class="muted">of</span>
          <input class="input mono task-num" data-change="week-required" data-key="${t.key}" value="${t.required}" inputmode="numeric" aria-label="Required">
          <span class="nowrap" style="flex:1;text-align:right;font-size:12.5px;color:var(--muted)">${t.remaining ? num(t.remaining) + ' left' : 'complete'}</span>
        </div>
        <div style="display:flex;gap:6px;justify-content:flex-end">
          ${[-1, 1, 5, 10].map(n => html`<button class="btn btn-secondary task-step" data-act="week-step" data-key="${t.key}" data-v="${n}" aria-label="${n > 0 ? 'Add ' + n : 'Subtract 1'}">${n > 0 ? '+' + n : '−1'}</button>`)}
        </div>
        ${!t.in_accepted && t.exportable ? html`<div style="font-size:12px" class="warn">${icon('warning')} Not on your Accepted Loot list</div>` : ''}
      </div>`)}</div>
    ${!d.tasks.length ? html`<div class="muted" style="padding:20px 4px">No tasks yet. Add the Delivery Tasks you were given this week to track what you still need.</div>` : ''}
    ${d.archive.length ? html`<details style="margin-top:26px"><summary style="cursor:pointer;font-weight:500">Previous weeks (${d.archive.length})</summary>
      <table class="table" style="margin-top:8px"><tbody>${d.archive.map(a => html`<tr>
        <td style="width:160px">Week of ${fmtDate(a.week_start, false)}</td>
        <td class="mono" style="width:90px">${a.done} / ${a.tasks}</td>
        <td style="font-size:12.5px;color:var(--muted)">${(a.items || []).map(i => `${i.name} ${i.collected}/${i.required}`).join(' · ')}</td></tr>`)}</tbody></table></details>` : ''}
  </div>`;
}

// ---------- hunt reports ------------------------------------------------------------

function viewHunts() {
  const h = S.hunt, a = h.analysis, s = a && a.session;
  const stat = (label, value, sub) => html`<div class="stat"><div class="kicker">${label}</div><div class="stat-value mono">${value}</div>${sub ? html`<div style="font-size:12px;color:var(--muted)">${sub}</div>` : ''}</div>`;
  return html`
  <div class="page" style="max-width:1120px">
    <h1>Hunt reports</h1>
    <div class="page-sub">Paste a session from Tibia’s Hunt Analyzer (“Copy to clipboard”) to value its loot with NPC prices. Only the text you paste is read.</div>
    <div class="panel" style="margin:18px 0;gap:10px">
      <textarea class="input mono" data-input="hunt-text" placeholder="Session data: From … to …&#10;Loot: …&#10;Looted Items:&#10;  3x dragon hams" style="min-height:120px;font-size:12.5px" aria-label="Hunt Analyzer text">${h.text}</textarea>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn btn-secondary" data-act="hunt-paste">${icon('clipboard-text')}Paste from clipboard</button>
        <button class="btn btn-primary" data-act="hunt-analyze" ${raw(h.text.trim() ? '' : 'disabled')}>${icon('chart-bar')}Analyze</button>
      </div>
      <div style="font-size:12px;color:var(--muted)">${icon('hard-drives')} Analyzed sessions are saved on this PC (the last 30) so you can open them again. Delete any of them below.</div>
    </div>
    ${a ? html`
      <div class="stat-row">
        ${stat('Session', s.duration || '—', s.from ? 'from ' + s.from : '')}
        ${stat('XP gain', num(s.xp))}
        ${stat('Loot (reported)', gp(s.loot), 'by the game’s own prices')}
        ${stat('NPC value', gp(a.npc_total), [a.unpriced ? `${plural(a.unpriced, 'item', 'items')} without an NPC price` : '', a.uncertain ? `${num(a.uncertain)} not counted until picked` : ''].filter(Boolean).join(' · ') || 'what NPCs pay')}
        ${stat('Balance', gp(s.balance), s.supplies !== null ? 'supplies ' + gp(s.supplies) : '')}
      </div>
      ${a.uncertain ? html`<div class="note warn-bg" style="margin:14px 0">${icon('question', 'class="warn"')}
        <div><div style="font-weight:500;margin-bottom:3px">${plural(a.uncertain, 'looted item shares', 'looted items share')} a name with other items</div>
        <div class="muted">Pick the right one in the list below. Until you do, ${a.uncertain === 1 ? 'it isn’t' : 'they aren’t'} counted in the NPC value or Weekly Tasks. Your pick is remembered for future reports.</div></div></div>` : ''}
      ${a.task_rows ? html`<div class="note" style="margin:14px 0">${icon('calendar-check', 'class="accent"')}
        <div style="flex:1"><div style="font-weight:500;margin-bottom:3px">${plural(a.task_rows, 'looted item counts', 'looted items count')} toward this week’s tasks</div>
        <div class="muted">${a.applied_this_week ? 'Already added to this week’s tasks.' : 'Add the looted amounts to your Weekly Tasks progress.'}</div></div>
        <button class="btn btn-primary" data-act="hunt-apply" ${raw(a.applied_this_week ? 'disabled' : '')} style="flex:none;align-self:center">Add to Weekly Tasks</button></div>` : ''}
      <table class="table" style="margin-top:12px">
        <thead><tr><th style="width:44px"></th><th>Item</th><th style="width:80px;text-align:right">Count</th><th style="width:110px;text-align:right">Each</th><th style="width:120px;text-align:right">Total</th><th style="width:150px">Accepted Loot</th></tr></thead>
        <tbody>${a.rows.map(r => html`<tr>
          <td>${spriteSlot(r.id, 28)}</td>
          <td>${r.name || r.line}
            ${r.uncertain ? html`<span class="tag tag-unverified tag-sm" style="margin-left:6px">pick one</span>` : ''}
            ${r.candidates.length ? html`<div><select class="input pick-select" data-change="hunt-choose" data-line="${r.line}" aria-label="Which ${r.line}?">
              ${r.uncertain ? html`<option value="" selected>Which one? Not counted yet</option>` : ''}
              ${r.candidates.map(c => html`<option value="${c.id}" ${raw(!r.uncertain && c.id === r.id ? 'selected' : '')}>${c.wiki || r.name} · ID ${c.id} · ${c.category}${c.each ? ' · NPC pays ' + num(c.each) + ' gp' : ''}${c.in_delivery ? ' · Delivery Task' : ''}</option>`)}
            </select></div>` : ''}
            ${r.id === null ? html`<span class="tag tag-conflicting tag-sm" style="margin-left:6px">not matched</span>` : ''}
            ${r.task ? html`<span class="tag tag-accent tag-sm" style="margin-left:6px">Task · ${num(r.task.remaining)} left</span>` : ''}</td>
          <td class="mono" style="text-align:right">${num(r.count)}</td>
          <td class="mono" style="text-align:right;color:${r.each && !r.uncertain ? 'var(--color-text)' : 'var(--muted)'}">${r.each ? gp(r.each) : '—'}</td>
          <td class="mono" style="text-align:right;${r.uncertain ? 'color:var(--muted);text-decoration:line-through' : ''}" ${raw(r.uncertain ? 'title="Not counted until you pick the item"' : '')}>${r.value ? gp(r.value) : '—'}</td>
          <td>${r.id === null || r.coin || r.uncertain ? '' : r.in_accepted ? html`<span class="ok" style="font-size:13px">${icon('check')} On the list</span>`
            : html`<button class="btn btn-ghost" data-act="hunt-accept" data-key="${r.id}" style="font-size:13px;padding-inline:8px">${icon('plus')}Add</button>`}</td>
        </tr>`)}</tbody>
      </table>
      ${a.unmatched.length ? html`<div style="font-size:12.5px;color:var(--muted);margin-top:8px">${icon('question')} Not matched to an item: ${a.unmatched.join(', ')}. These aren’t counted in the NPC value.</div>` : ''}
      ${s.monsters.length ? html`<div style="font-size:12.5px;color:var(--muted);margin-top:8px">Killed: ${s.monsters.slice(0, 10).map(m => `${num(m.count)}× ${m.name}`).join(', ')}${s.monsters.length > 10 ? '…' : ''}</div>` : ''}
    ` : ''}
    ${h.list.length ? html`<h5 style="margin:28px 0 6px">Saved sessions</h5>
      <table class="table"><tbody>${h.list.map(x => html`<tr>
        <td>${x.from || fmtDate(x.imported_at)}</td><td class="mono" style="width:90px">${x.duration || '—'}</td>
        <td class="mono" style="width:140px;text-align:right">${gp(x.loot)}</td><td class="mono" style="width:140px;text-align:right">${gp(x.balance)}</td>
        <td style="width:170px;text-align:right"><button class="btn btn-ghost" data-act="hunt-open" data-v="${x.id}" style="font-size:13px;padding-inline:8px">Open</button>
          <button class="btn btn-ghost" data-act="hunt-delete" data-v="${x.id}" style="font-size:13px;padding-inline:8px;color:var(--bad)">Delete</button></td></tr>`)}</tbody></table>` : ''}
  </div>`;
}

// ---------- export ------------------------------------------------------------------

function viewExport() {
  const d = S.exp.data;
  if (!d) return '';
  return html`
  <div class="page">
    <h1>Copy &amp; export</h1>
    <div class="page-sub" style="margin-bottom:20px">Two ways to get ${num(d.count)} items into the game.</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px;max-width:1120px">
      <section style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:10px">${icon('list-checks', 'class="accent" style="font-size:20px"')}<h4 style="margin:0">Manual checklist</h4></div>
        <p style="font-size:13px;color:var(--muted);margin:0">Tibia’s official guide describes managing Accepted Loot in the Cyclopedia. Use this list as a checklist while you add items there. Pasting names into the game is not a verified workflow.</p>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:13px">
          <label style="display:flex;gap:6px;align-items:center">Sort
            <select class="input" data-change="exp-sort" style="width:auto;min-height:30px;padding:3px 8px;font-size:13px">
              <option value="name" ${raw(S.exp.sort === 'name' ? 'selected' : '')}>Name</option>
              <option value="category" ${raw(S.exp.sort === 'category' ? 'selected' : '')}>Category, then name</option>
            </select></label>
          <label style="display:flex;gap:6px;align-items:center;cursor:pointer"><input type="checkbox" data-change="exp-ids" ${raw(S.exp.ids ? 'checked' : '')}>Include item IDs</label>
        </div>
        <div class="codebox">
          <div style="display:flex;align-items:center;gap:8px;padding:8px 10px 8px 14px;font-size:12px;color:var(--muted)">
            <span style="flex:1">One item per line · ${plural(d.lines.length, 'line', 'lines')}</span>
            <button class="btn btn-secondary" data-act="exp-save-text" style="font-size:12.5px;padding:5px 10px">${icon('floppy-disk')}Save…</button>
            <button class="btn btn-secondary" data-act="exp-copy" style="font-size:12.5px;padding:5px 10px">${icon(S.exp.copied ? 'check' : 'copy')}${S.exp.copied ? 'Copied' : 'Copy list'}</button>
          </div>
          <pre style="max-height:340px">${d.lines.join('\n')}</pre>
        </div>
        <a href="#" data-act="open-url" data-url="https://www.tibia.com/gameguides/?section=controls&amp;subtopic=manual" style="font-size:12.5px;display:flex;align-items:center;gap:6px;text-decoration:none">${icon('arrow-square-out')}Official Quick Loot guide on tibia.com</a>
      </section>
      <section style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;gap:10px">${icon('file-code', 'class="accent" style="font-size:20px"')}<h4 style="margin:0">Game file</h4></div>
        <div style="display:flex;gap:8px;align-items:center;font-size:13px">${d.format_ok
          ? html`${icon('seal-check', 'class="ok" style="font-size:17px"')}Format validated against your client ${d.client_version || ''} files`
          : html`${icon('warning', 'class="warn" style="font-size:17px"')}${d.format_message}`}</div>
        <div class="codebox">
          <div class="mono" style="padding:8px 14px;font-size:12px;color:var(--muted)">lootBlackWhitelist.json</div>
          <pre>${d.json_preview}</pre>
        </div>
        ${d.blocked.length ? html`<div class="note warn-bg" style="font-size:13px;padding:12px 14px">${icon('warning', 'class="warn" style="font-size:18px"')}<span>${plural(d.blocked.length, 'item', 'items')} with unverified or conflicting IDs will be left out: ${d.blocked.slice(0, 6).join(', ')}${d.blocked.length > 6 ? '…' : ''}. ${num(d.export_count)} items will be written.</span></div>`
          : html`<div style="font-size:13px;color:var(--muted)">${num(d.export_count)} items will be written. The exported file has an empty Skipped list; to keep a character’s settings, install instead.</div>`}
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary" data-act="exp-file" ${raw(d.format_ok && d.export_count ? '' : 'disabled')}>${icon('download-simple')}Export file…</button>
          <button class="btn btn-secondary" data-act="go" data-screen="install">Install to a character instead</button>
        </div>
      </section>
    </div>
  </div>`;
}

// ---------- install -----------------------------------------------------------------

function viewInstall() {
  const d = S.ins.data;
  if (!d) return '';
  const sel = d.characters.find(c => c.id === d.selected);
  const selName = sel ? (sel.label || 'Folder ' + sel.id) : '';
  const canInstall = d.format_ok && sel && !sel.error && d.export_count > 0;
  const choice = (mode, title, desc) => html`
    <div class="choice ${S.ins.mode === mode ? 'on' : ''}" data-act="ins-mode" data-v="${mode}" role="radio" aria-checked="${S.ins.mode === mode}" tabindex="0">
      <i class="${S.ins.mode === mode ? 'ph-fill ph-radio-button' : 'ph ph-circle'} pick"></i>
      <div><div style="font-weight:500;font-size:14px">${title}</div><div style="font-size:12.5px;color:var(--muted)">${desc}</div></div>
    </div>`;
  return html`
  <div class="page">
    <h1>Install to character</h1>
    <div class="page-sub" style="margin-bottom:18px">Writes your list into one character’s loot file, with a preview and a backup.</div>
    <div class="field" style="max-width:860px">
      <label for="ins-folder">Tibia characterdata folder</label>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input id="ins-folder" class="input mono" readonly value="${d.folder || ''}" style="flex:1;min-width:300px;font-size:12.5px">
        <button class="btn btn-secondary" data-act="folder-browse">Browse…</button>
        <button class="btn btn-ghost" data-act="folder-default" style="padding-inline:10px">Use default</button>
      </div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;font-size:13px;margin:12px 0 22px">${d.format_ok ? icon('seal-check', 'class="ok" style="font-size:17px"') : icon('warning', 'class="warn" style="font-size:17px"')}${d.format_message}</div>
    <div style="display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr);gap:24px;align-items:start">
      <div>
        <div style="font-size:13px;color:var(--muted);margin-bottom:8px;max-width:540px">Folders are named by number. The app can’t tell which character each belongs to, so give them labels you’ll recognise.</div>
        <table class="table">
          <thead><tr><th style="width:28px"></th><th>Folder</th><th>Your label</th><th>Mode</th><th style="text-align:right">Accepted</th><th style="text-align:right">Skipped</th></tr></thead>
          <tbody>${d.characters.map(c => html`
            <tr data-act="ins-select" data-v="${c.id}" style="cursor:pointer;box-shadow:${c.id === d.selected ? 'inset 0 0 0 1px var(--color-accent)' : 'none'}">
              <td><i class="${c.id === d.selected ? 'ph-fill ph-radio-button' : 'ph ph-circle'}" style="color:var(--color-accent);font-size:16px"></i></td>
              <td class="mono" style="font-size:12.5px">${c.id}</td>
              <td>${S.ins.editing === c.id
                ? html`<input class="input label-input" data-input="label" data-folder="${c.id}" value="${c.label}" placeholder="e.g. character name" maxlength="60" data-autofocus>`
                : html`<span style="color:${c.label ? 'var(--color-text)' : 'var(--muted)'}">${c.label || 'Add a label'}</span> <button class="icon-btn" data-act="ins-edit" data-v="${c.id}" title="Edit label" aria-label="Edit label for ${c.id}" style="display:inline-grid;width:24px;height:24px;font-size:13px;vertical-align:middle">${icon('pencil-simple')}</button>`}</td>
              <td style="font-size:13px;${c.error ? 'color:var(--bad)' : ''}" title="${c.error || ''}">${c.mode}</td>
              <td class="mono" style="text-align:right;font-size:12.5px">${c.accepted ?? '—'}</td>
              <td class="mono" style="text-align:right;font-size:12.5px">${c.skipped ?? '—'}</td>
            </tr>`)}</tbody>
        </table>
        ${!d.characters.length ? html`<div style="padding:18px 8px;color:var(--muted)">No character folders here. Choose the folder that contains your numbered character folders.</div>` : ''}
        ${sel ? html`<div style="margin-top:26px">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><h5 style="margin:0;flex:1">Backups for ${selName}</h5>
            <button class="btn btn-ghost" data-act="open-folder" data-which="backups" data-folder="${sel.id}" style="font-size:13px;padding-inline:8px">${icon('folder-open')}Open backups folder</button></div>
          <table class="table"><tbody>${d.backups.map(b => html`
            <tr><td class="mono" style="font-size:12px">${b.file}</td><td style="font-size:12.5px;color:var(--muted)">${fmtDate(b.saved)}</td>
              <td style="text-align:right"><button class="btn btn-ghost" data-act="restore-open" data-file="${b.file}" style="font-size:13px;padding-inline:8px">Restore…</button></td></tr>`)}</tbody></table>
          ${!d.backups.length ? html`<div style="font-size:13px;color:var(--muted);padding:8px">No backups yet. One is made before every install.</div>` : ''}
        </div>` : ''}
      </div>
      <div class="panel">
        <h5 style="margin:0">Install your Accepted Loot list</h5>
        <div style="font-size:13px;color:var(--muted)">To <span style="color:var(--color-text)">${selName || '—'}</span> · ${num(d.export_count)} exportable items${d.blocked ? ` · ${d.blocked} left out` : ''}</div>
        ${choice('merge', 'Merge', 'Keep the items already on the character’s Accepted list and add yours.')}
        ${choice('replace', 'Replace', 'The character’s Accepted list becomes exactly your list.')}
        <div style="font-size:12.5px;color:var(--muted)">Both switch the character to Accepted Loot mode and leave the Skipped list as it is.</div>
        <button class="btn btn-primary" data-act="install-preview" ${raw(canInstall ? '' : 'disabled')} style="padding:9px 14px">${icon('eye')}Preview changes…</button>
        ${!d.format_ok ? html`<div style="font-size:12.5px" class="warn">Installing is disabled until the loot file format is confirmed.</div>` : sel && sel.error ? html`<div style="font-size:12.5px" class="bad">This character’s loot file can’t be read.</div>` : ''}
      </div>
    </div>
  </div>`;
}

// ---------- sources -----------------------------------------------------------------

function viewSources() {
  const d = S.src.data;
  if (!d) return '';
  return html`
  <div class="page" style="max-width:1000px">
    <h1>Data sources</h1>
    <div class="page-sub" style="margin-bottom:20px">Where the catalog and Delivery Task list come from, and how fresh they are.</div>
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:16px 18px;border-radius:14px;background:var(--color-surface);margin-bottom:26px">
      <div style="flex:1;min-width:260px"><div style="font-weight:500">Last successful update ${fmtDate(d.last_update)}</div>
        <div style="font-size:12.5px;color:var(--muted);margin-top:2px">Updates only run when you click. Nothing changes until you review the results. TibiaWiki is read at about one request per second.</div></div>
      <button class="btn btn-primary" data-act="update-start" style="padding:9px 16px">${icon('arrows-clockwise')}Check for updates</button>
    </div>
    <h5 style="margin:0 0 6px">In use</h5>
    <table class="table" style="margin-bottom:26px">
      <thead><tr><th>Source</th><th style="width:160px">Type</th><th>Provides</th><th>Freshness</th><th style="width:120px">Status</th></tr></thead>
      <tbody>${d.rows.map(s => html`
        <tr>
          <td><div style="font-size:14px">${s.name}</div><div style="font-size:12px;color:var(--muted)">${s.where}</div></td>
          <td><span class="tag tag-sm nowrap ${s.official ? 'tag-accent' : 'tag-neutral'}">${s.type}</span></td>
          <td style="font-size:13px;color:var(--muted);max-width:240px">${s.gives}</td>
          <td style="font-size:12.5px">${s.off ? '—' : html`${fmtDate(s.last_success)}${s.bundled ? html`<div style="color:var(--muted)">bundled copy</div>` : ''}`}</td>
          <td>${s.off ? html`<span style="display:flex;gap:6px;align-items:center;font-size:12.5px;color:var(--muted)">${icon('minus-circle')}Off</span>`
            : s.error ? html`<span style="display:flex;gap:6px;align-items:center;font-size:12.5px" class="warn" title="${s.error}">${icon('warning')}Last check failed</span>`
            : html`<span style="display:flex;gap:6px;align-items:center;font-size:12.5px" class="ok">${icon('check-circle')}OK</span>`}</td>
        </tr>`)}</tbody>
    </table>
    ${d.rows.filter(s => s.error).map(s => html`<div class="note warn-bg" style="margin:-14px 0 20px;padding:10px 14px">${icon('warning', 'class="warn" style="font-size:17px"')}<span>${s.name}: ${s.error}. Cached data is kept.</span></div>`)}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px">
      <div>
        <h5 style="margin:0 0 6px">Evaluated, not used</h5>
        <div style="display:grid;gap:10px;font-size:13px">
          <div><div>tibiawiki.dev API</div><div style="color:var(--muted);font-size:12.5px">Unofficial mirror of the same wiki fields. Its bulk export stops at 5,000 pages, so it can’t provide the full index.</div></div>
          <div><div>TibiaWiki BR · Weekly Tasks</div><div style="color:var(--muted);font-size:12.5px">Portuguese reference, adds no extra fields.</div></div>
          <div><div>TibiaPal Deliveries <span class="tag tag-neutral" style="padding:1px 7px;margin-left:4px">Third-party estimate</span></div><div style="color:var(--muted);font-size:12.5px">Market reference only. Would be shown as an estimate, never as authoritative. Not maintained for Summer 2026 items.</div></div>
        </div>
      </div>
      <div>
        <h5 style="margin:0 0 6px">Settings</h5>
        <div class="field" style="margin-bottom:12px">
          <label for="limit">List-size warning (optional)</label>
          <div style="display:flex;gap:8px"><input id="limit" class="input mono" data-input="limit" inputmode="numeric" placeholder="No limit set" value="${d.limit ?? ''}" style="max-width:180px">
            <button class="btn btn-secondary" data-act="limit-save">Save</button></div>
          <div style="font-size:12px;color:var(--muted);margin-top:5px">No verified client limit is built in.</div>
        </div>
        <div style="font-size:12.5px;color:var(--muted)">Edits, cache and backups are stored in <a href="#" data-act="open-folder" data-which="data" class="mono" style="color:var(--color-text)">${d.app_data}</a></div>
      </div>
    </div>
  </div>`;
}

// ---------- help --------------------------------------------------------------------

function viewHelp() {
  const d = S.help.data;
  if (!d) return '';
  return html`
  <div class="page" style="display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:32px;align-items:start;max-width:1120px">
    <div>
      <h1>Help &amp; Support</h1>
      <div class="page-sub" style="margin-bottom:22px">Answers to common questions, what changed, and how to report a problem.</div>
      <h5 style="margin:0 0 6px">Frequently asked questions</h5>
      <div style="display:flex;flex-direction:column;margin-bottom:28px">
        ${d.faq.map((f, i) => html`<div class="rule">
          <button data-act="faq" data-v="${i}" aria-expanded="${S.help.open === i}" style="width:100%;display:flex;align-items:center;gap:10px;padding:12px 4px;border:0;background:transparent;color:var(--color-text);font-size:14px;text-align:left;cursor:pointer">
            <i class="ph-bold ${S.help.open === i ? 'ph-caret-down' : 'ph-caret-right'}" style="color:var(--color-accent);font-size:14px;flex:none"></i><span style="flex:1">${f.q}</span></button>
          ${S.help.open === i ? html`<div style="padding:0 4px 14px 28px;font-size:13.5px;line-height:1.6;color:var(--muted);max-width:640px">${f.a}</div>` : ''}
        </div>`)}
      </div>
      <h5 style="margin:0 0 10px">Release notes</h5>
      <div style="display:flex;flex-direction:column;gap:18px">${d.releases.map(r => html`
        <div style="display:grid;grid-template-columns:110px minmax(0,1fr);gap:14px">
          <div><div class="mono" style="font-size:13px">${r.version}</div><div style="font-size:12px;color:var(--muted)">${fmtDate(r.date, false)}</div></div>
          <ul style="margin:0;padding-left:18px;font-size:13.5px;line-height:1.6;display:flex;flex-direction:column;gap:4px">${r.notes.map(n => html`<li>${n}</li>`)}</ul>
        </div>`)}</div>
    </div>
    <aside class="panel" style="gap:8px;position:sticky;top:24px">
      <h5 style="margin:0 0 6px">Support</h5>
      <button class="btn btn-secondary" data-act="contact" ${raw(d.support.has_contact ? '' : 'disabled')} style="justify-content:flex-start">${icon('chat-circle')}Contact support</button>
      <button class="btn btn-secondary" data-act="report-open" data-category="bug" style="justify-content:flex-start">${icon('bug')}Report a bug</button>
      <button class="btn btn-secondary" data-act="report-open" data-category="item_id" style="justify-content:flex-start">${icon('flag')}Report incorrect item data</button>
      <button class="btn btn-secondary" data-act="report-open" data-category="install" style="justify-content:flex-start">${icon('download-simple')}Report an install or export problem</button>
      <button class="btn btn-secondary" data-act="report-open" data-category="feature" style="justify-content:flex-start">${icon('lightbulb')}Suggest a feature</button>
      <button class="btn btn-ghost" data-act="open-folder" data-which="reports" style="justify-content:flex-start;padding-inline:10px;margin-top:4px">${icon('folder-open')}Open saved reports folder</button>
      <div style="font-size:12.5px;color:var(--muted);line-height:1.5;margin-top:6px">${d.support.can_send || d.support.has_contact
        ? 'Reports open in your browser or e-mail program, where you review and submit them.'
        : html`No support destination is configured for this copy of the app. Reports can still be copied or saved. Whoever distributes the app can set one in <span class="mono" style="color:var(--color-text)">data/support.json</span>.`}</div>
    </aside>
  </div>`;
}

// ---------- modals ------------------------------------------------------------------

function viewModal() {
  const m = S.modal;
  if (!m) return '';
  const views = { profiles: viewProfilesModal, 'profile-new': viewProfileNewModal, history: viewHistoryModal,
    update: viewUpdateModal, install: viewInstallModal, restore: viewRestoreModal, report: viewReportModal,
    confirm: viewConfirmModal, error: viewErrorModal, 'search-save': viewSearchSaveModal, level: viewLevelModal };
  return html`<div class="backdrop" data-backdrop role="dialog" aria-modal="true">${views[m.kind](m)}</div>`;
}

function viewProfileNewModal(m) {
  const opt = (v, title, desc) => html`
    <div class="choice ${m.start === v ? 'on' : ''}" data-act="profile-start" data-v="${v}" role="radio" aria-checked="${m.start === v}" tabindex="0">
      <i class="${m.start === v ? 'ph-fill ph-radio-button' : 'ph ph-circle'} pick"></i>
      <div><div style="font-weight:500;font-size:14px">${title}</div><div style="font-size:12.5px;color:var(--muted)">${desc}</div></div>
    </div>`;
  return html`<div class="dialog" style="width:min(520px,100%)">
    <div class="dialog-title">New profile</div>
    <div class="field"><label for="profile-name">Name</label>
      <input id="profile-name" class="input" data-input="profile-name" value="${m.name}" maxlength="40" placeholder="e.g. Elite Knight, Soul War hunts" data-autofocus></div>
    <div style="display:grid;gap:8px">
      ${opt('delivery', 'Start with the Delivery Task list', 'Like a fresh start: every Delivery Task item, then your changes.')}
      ${opt('empty', 'Start empty', 'Add items yourself from the catalog.')}
      ${opt('copy', 'Copy my current list', `Everything on “${(S.app.profile || {}).name || 'the current profile'}” right now.`)}
    </div>
    <div class="dialog-actions"><button class="btn btn-secondary" data-act="modal-close">Cancel</button>
      <button class="btn btn-primary" data-act="profile-create">Create and switch</button></div></div>`;
}

function viewProfilesModal(m) {
  const d = m.data;
  if (!d) return html`<div class="dialog"><div class="dialog-title">Profiles</div><div class="muted">Loading…</div></div>`;
  const names = Object.fromEntries(d.profiles.map(p => [p.id, p.name]));
  return html`<div class="dialog" style="width:min(820px,100%);gap:16px">
    <div style="display:flex;align-items:flex-start;gap:12px">
      <div style="flex:1"><div class="dialog-title">Profiles</div><div style="font-size:13px;color:var(--muted);margin-top:2px">Each profile is its own Accepted Loot list. The Delivery Task list is shared by all of them.</div></div>
      <button class="btn btn-icon btn-ghost" data-act="modal-close" title="Close" aria-label="Close" style="color:var(--color-text)">${icon('x', 'style="font-size:16px"')}</button>
    </div>
    <table class="table">
      <thead><tr><th>Name</th><th style="width:90px;text-align:right">Items</th><th style="width:100px;text-align:right">Exportable</th><th style="width:330px"></th></tr></thead>
      <tbody>${d.profiles.map(p => html`<tr>
        <td>${m.renaming === p.id
          ? html`<input class="input label-input" data-input="profile-rename" data-id="${p.id}" value="${p.name}" maxlength="40" data-autofocus>`
          : html`${p.name} ${p.active ? html`<span class="tag tag-accent tag-sm" style="margin-left:6px">Active</span>` : ''}
                 <button class="icon-btn" data-act="profile-rename" data-v="${p.id}" title="Rename" aria-label="Rename ${p.name}" style="display:inline-grid;width:24px;height:24px;font-size:13px;vertical-align:middle">${icon('pencil-simple')}</button>`}</td>
        <td class="mono" style="text-align:right;font-size:12.5px">${num(p.count)}</td>
        <td class="mono" style="text-align:right;font-size:12.5px">${num(p.exportable)}</td>
        <td style="text-align:right;white-space:nowrap">
          ${p.active ? '' : html`<button class="btn btn-ghost" data-act="profile-switch-to" data-v="${p.id}" style="font-size:13px;padding-inline:8px">Switch</button>`}
          <button class="btn btn-ghost" data-act="profile-duplicate" data-v="${p.id}" style="font-size:13px;padding-inline:8px">Duplicate</button>
          <button class="btn btn-ghost" data-act="profile-export" data-v="${p.id}" style="font-size:13px;padding-inline:8px">Export…</button>
          ${d.profiles.length > 1 ? html`<button class="btn btn-ghost" data-act="profile-delete" data-v="${p.id}" style="font-size:13px;padding-inline:8px;color:var(--bad)">Delete</button>` : ''}
        </td></tr>`)}</tbody>
    </table>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px">
      <div class="panel" style="gap:8px;padding:14px">
        <h6 style="margin:0">Import</h6>
        <button class="btn btn-secondary" data-act="profile-import" style="justify-content:flex-start;font-size:13px">${icon('file-arrow-down')}From a profile file…</button>
        ${d.characters.length ? html`<div style="display:flex;gap:6px">
          <select class="input" data-change="profile-char" style="min-height:32px;padding:4px 8px;font-size:13px">${d.characters.map(c => html`<option value="${c.id}" ${raw(m.char === c.id ? 'selected' : '')}>${c.label || 'Folder ' + c.id} · ${num(c.accepted)} items</option>`)}</select>
          <button class="btn btn-secondary" data-act="profile-from-char" style="font-size:13px;flex:none">From character</button></div>
          <div style="font-size:12px;color:var(--muted)">Copies a character’s current in-game Accepted list into a new profile.</div>` : ''}
      </div>
      <div class="panel" style="gap:8px;padding:14px">
        <h6 style="margin:0">Compare</h6>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <select class="input" data-change="profile-cmp-a" style="width:auto;min-height:32px;padding:4px 8px;font-size:13px">${d.profiles.map(p => html`<option value="${p.id}" ${raw(m.cmpA === p.id ? 'selected' : '')}>${p.name}</option>`)}</select>
          <span class="muted">vs</span>
          <select class="input" data-change="profile-cmp-b" style="width:auto;min-height:32px;padding:4px 8px;font-size:13px">${d.profiles.map(p => html`<option value="${p.id}" ${raw(m.cmpB === p.id ? 'selected' : '')}>${p.name}</option>`)}</select>
          <button class="btn btn-secondary" data-act="profile-compare" style="font-size:13px">Compare</button>
        </div>
        ${m.cmp ? html`<div style="font-size:12.5px;display:grid;gap:6px;max-height:160px;overflow:auto">
          <div>${num(m.cmp.both)} items on both.</div>
          <div><b>Only on ${m.cmp.a} (${num(m.cmp.only_a.length)}):</b> <span class="muted">${m.cmp.only_a.slice(0, 40).join(', ') || '—'}${m.cmp.only_a.length > 40 ? '…' : ''}</span></div>
          <div><b>Only on ${m.cmp.b} (${num(m.cmp.only_b.length)}):</b> <span class="muted">${m.cmp.only_b.slice(0, 40).join(', ') || '—'}${m.cmp.only_b.length > 40 ? '…' : ''}</span></div>
        </div>` : ''}
      </div>
    </div></div>`;
}

function viewHistoryModal(m) {
  const d = m.data;
  return html`<div class="dialog" style="width:min(640px,100%)">
    <div style="display:flex;align-items:flex-start;gap:12px">
      <div style="flex:1"><div class="dialog-title">History · ${(S.app.profile || {}).name || ''}</div>
        <div style="font-size:13px;color:var(--muted);margin-top:2px">Every change to this profile’s list. Restoring a version is itself recorded, so it can be undone.</div></div>
      <button class="btn btn-icon btn-ghost" data-act="modal-close" title="Close" aria-label="Close" style="color:var(--color-text)">${icon('x', 'style="font-size:16px"')}</button>
    </div>
    <div style="max-height:420px;overflow:auto">${!d ? html`<div class="muted">Loading…</div>` : !d.entries.length ? html`<div class="muted">No changes recorded yet.</div>` :
      d.entries.map((e, i) => html`<div class="dlg-row rule">
        ${icon(i === 0 ? 'circle-wavy-check' : 'clock', `class="${i === 0 ? 'ok' : 'muted'}" style="font-size:16px"`)}
        <div><div style="font-size:14px">${e.description}</div><div style="font-size:12px;color:var(--muted)">${fmtDate(e.at)}${e.count !== null && e.count !== undefined ? ' · ' + plural(e.count, 'item', 'items') : ''}</div></div>
        ${i === 0 ? html`<span class="muted" style="font-size:12.5px">Current</span>` : html`<button class="btn btn-ghost" data-act="history-restore" data-v="${e.index}" style="font-size:13px;padding-inline:8px">Restore</button>`}
      </div>`)}</div></div>`;
}

function nameList(names, limit = 60) {
  if (!names.length) return html`<span class="muted">—</span>`;
  return html`<span>${names.slice(0, limit).join(', ')}${names.length > limit ? html` <span class="muted">and ${num(names.length - limit)} more</span>` : ''}</span>`;
}

function viewLevelModal(m) {
  const p = m.preview;
  const title = m.review ? `Update the ${m.name} level?` : p ? (p.level ? `Use the ${p.name} level?` : 'Stop using a strictness level?') : 'Strictness level';
  return html`<div class="dialog" style="width:min(640px,100%)">
    <div class="dialog-title">${title}</div>
    ${!p ? html`<div class="muted">Working out the changes…</div>` : html`
      <div style="font-size:13.5px">${m.review
        ? 'Item prices or tier rules changed since you chose this level. These are the items it now adds or drops:'
        : html`Your list would have <b class="mono">${num(p.total_after)}</b> items${p.manual_added || p.manual_removed ? html`, including your ${plural(p.manual_added, 'own addition', 'own additions')} and leaving out your ${plural(p.manual_removed, 'removal', 'removals')}` : ''}.`}</div>
      <div style="display:grid;gap:10px;font-size:13px;max-height:300px;overflow:auto">
        <div><div class="ok" style="font-weight:500;margin-bottom:3px">${icon('plus-circle')} Added (${num(p.add.length)})</div>${nameList(p.add)}</div>
        <div><div class="bad" style="font-weight:500;margin-bottom:3px">${icon('minus-circle')} Removed (${num(p.remove.length)})</div>${nameList(p.remove)}</div>
      </div>
      ${p.limit && p.limit.warn ? html`<div class="note warn-bg">${icon('gauge', 'class="warn"')}<div><div style="font-weight:500;margin-bottom:3px">${p.limit.title}</div><div class="muted">${p.limit.text}</div></div></div>` : ''}
      <div style="font-size:12.5px;color:var(--muted)">Levels use the highest NPC buy price from your installed client, or the item’s Market category when no NPC buys it. You can undo this from the profile’s history.</div>`}
    <div class="dialog-actions"><button class="btn btn-secondary" data-act="modal-close">Cancel</button>
      <button class="btn btn-primary" data-act="${m.review ? 'level-accept' : 'level-apply'}" ${raw(p ? '' : 'disabled')} data-autofocus>${m.review ? 'Accept changes' : p && p.level ? `Use ${p.name}` : 'Stop using a level'}</button></div></div>`;
}

function viewSearchSaveModal(m) {
  return html`<div class="dialog" style="width:min(460px,100%)">
    <div class="dialog-title">Save this search</div>
    <div style="font-size:13px;color:var(--muted)">Saves the search text and filters so you can apply them again with one click.</div>
    <div class="field"><label for="search-name">Name</label>
      <input id="search-name" class="input" data-input="search-name" value="${m.name}" maxlength="40" placeholder="e.g. Rare armors" data-autofocus></div>
    <div class="dialog-actions"><button class="btn btn-secondary" data-act="modal-close">Cancel</button>
      <button class="btn btn-primary" data-act="search-save">Save</button></div></div>`;
}

function viewConfirmModal(m) {
  return html`<div class="dialog" style="width:min(480px,100%)">
    <div class="dialog-title">${m.title}</div><div class="dialog-body">${m.body}</div>
    <div class="dialog-actions"><button class="btn btn-secondary" data-act="modal-close">${m.cancelLabel || 'Cancel'}</button>
      <button class="btn btn-primary" data-act="confirm-ok" data-autofocus>${m.ok}</button></div></div>`;
}

function viewErrorModal(m) {
  return html`<div class="dialog" style="width:min(520px,100%)">
    <div style="display:flex;gap:10px;align-items:center">${icon('warning-circle', 'class="bad" style="font-size:22px"')}<div class="dialog-title">${m.title}</div></div>
    <div class="dialog-body" style="white-space:pre-wrap">${m.message}</div>
    <div class="dialog-actions">
      <button class="btn btn-secondary" data-act="error-report">${icon('flag')}Report this problem</button>
      <button class="btn btn-primary" data-act="modal-close" data-autofocus>OK</button></div></div>`;
}

function viewUpdateModal(m) {
  if (m.running || !m.review) {
    return html`<div class="dialog" style="width:min(520px,100%)">
      <div class="dialog-title">Checking for updates</div>
      <div style="display:flex;gap:10px;align-items:center;font-size:13.5px"><i class="ph ph-circle-notch spin accent" style="font-size:18px"></i><span>${m.progress || 'Starting…'}</span></div>
      <div style="font-size:12.5px;color:var(--muted)">TibiaWiki is read at about one request per second. Nothing is changed until you review the results.</div>
      <div class="dialog-actions"><button class="btn btn-secondary" data-act="update-cancel">Cancel</button></div></div>`;
  }
  const r = m.review, tab = m.tab || 'new';
  const labels = [['new', 'New'], ['changed', 'Changed'], ['removed', 'Removed']];
  const rows = r.tabs[tab];
  const toneIcon = { new: ['plus-circle', 'ok'], changed: ['pencil-simple', 'accent'], removed: ['minus-circle', 'bad'] }[tab];
  return html`<div class="dialog" style="width:min(720px,100%)">
    <div><div class="dialog-title">Review updates</div><div style="font-size:13px;color:var(--muted);margin-top:2px">Checked ${fmtDate(new Date().toISOString())} · nothing is applied until you confirm</div></div>
    <div style="display:grid;gap:6px;font-size:12.5px">${r.results.map(x => html`<div style="display:flex;gap:8px;align-items:center">
      ${icon(x.ok ? 'check-circle' : 'warning', `class="${x.ok ? 'ok' : 'warn'}" style="font-size:16px;flex:none"`)}<span>${x.label}: ${x.ok ? 'retrieved' : 'could not be updated — cached data is kept. ' + x.error}</span></div>`)}</div>
    <div class="seg" style="align-self:flex-start" role="radiogroup">${labels.map(([k, l]) => html`
      <label class="seg-opt nowrap"><input type="radio" name="updtab" data-act="update-tab" data-v="${k}" ${raw(tab === k ? 'checked' : '')}>${l}<span class="mono" style="font-size:11.5px;color:var(--muted)">${num(r.tabs[k].length)}</span></label>`)}</div>
    <div style="display:flex;flex-direction:column;min-height:180px;max-height:340px;overflow:auto">
      ${rows.slice(0, 300).map(u => html`<div class="rule" style="display:grid;grid-template-columns:20px minmax(0,1fr);gap:10px;padding:10px 4px">
        ${icon(toneIcon[0], `class="${toneIcon[1]}" style="font-size:16px;margin-top:2px"`)}
        <div><div style="font-size:14px">${u.name} <span style="font-size:11.5px;color:var(--muted)">· ${u.area}</span></div>
          ${u.detail ? html`<div style="font-size:12.5px;color:var(--muted)">${u.detail}</div>` : ''}
          ${u.note ? html`<div style="font-size:12.5px;color:var(--color-accent-300);margin-top:3px;display:flex;gap:6px;align-items:center">${icon('shield-check')}${u.note}</div>` : ''}</div></div>`)}
      ${rows.length > 300 ? html`<div style="padding:10px 4px;font-size:12.5px;color:var(--muted)">…and ${num(rows.length - 300)} more.</div>` : ''}
      ${!rows.length ? html`<div style="padding:18px 4px;color:var(--muted);font-size:13px">No ${tab} records.</div>` : ''}
    </div>
    ${r.removed_delivery ? html`<label style="display:flex;gap:8px;align-items:center;font-size:13px;cursor:pointer"><input type="checkbox" data-change="update-keep" ${raw(m.keep ? 'checked' : '')}>Keep the ${plural(r.removed_delivery, 'item', 'items')} removed from the source on my Delivery Task list</label>` : ''}
    <div style="font-size:12.5px;color:var(--muted);display:flex;gap:6px;align-items:center">${icon('shield-check', 'class="accent"')}Your ${plural(r.kept.excluded, 'exclusion', 'exclusions')} and ${plural(r.kept.added, 'addition', 'additions')} are kept as they are.</div>
    <div class="dialog-actions">
      <button class="btn btn-secondary" data-act="update-cancel">Cancel</button>
      ${r.any_success ? html`<button class="btn btn-primary" data-act="update-apply" data-autofocus>Apply changes</button>` : ''}
    </div></div>`;
}

function viewInstallModal(m) {
  const p = m.preview, toneColor = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)', muted: 'var(--muted)', accent: 'var(--color-accent)' };
  return html`<div class="dialog" style="width:min(620px,100%)">
    <div><div class="dialog-title">Preview: ${p.mode === 'merge' ? 'Merge' : 'Replace'}</div>
      <div style="font-size:13px;color:var(--muted);margin-top:2px">${p.label || 'Unlabelled'} · folder <span class="mono">${p.folder}</span> · ${num(p.total)} items after install</div></div>
    <div>${p.rows.map(r => html`<div class="dlg-row rule">${icon(r.icon, `style="color:${toneColor[r.tone]};font-size:16px"`)}<span style="font-size:14px">${r.label}</span><span class="mono" style="font-size:12.5px;color:var(--muted)">${r.value}</span></div>`)}</div>
    ${p.removed.length ? html`<div class="note bad-bg" style="display:block;padding:10px 12px;font-size:12.5px"><div style="font-weight:500;margin-bottom:4px">${plural(p.removed.length, 'item', 'items')} will be removed from the Accepted list</div><div style="color:var(--muted)">${p.removed.slice(0, 30).join(', ')}${p.removed.length > 30 ? '…' : ''}</div></div>` : ''}
    ${p.blocked.length ? html`<div style="font-size:12.5px;color:var(--muted)">${icon('warning', 'class="warn"')} Not installed (ID unverified or conflicting): ${p.blocked.slice(0, 8).join(', ')}${p.blocked.length > 8 ? '…' : ''}</div>` : ''}
    ${p.also_skipped.length ? html`<div style="font-size:12.5px;color:var(--muted)">${num(p.also_skipped.length)} item(s) are also on the Skipped list (${p.also_skipped.slice(0, 5).join(', ')}). In Accepted Loot mode the Skipped list is not used.</div>` : ''}
    <div style="display:grid;gap:8px;font-size:12.5px">
      <div style="display:flex;gap:8px;align-items:center">${p.tibia_running ? html`${icon('warning-circle', 'class="bad" style="font-size:16px"')}<span><b>Tibia is running.</b> Close the game yourself, then check again. The app never closes it for you.</span>` : p.tibia_unknown ? html`${icon('warning', 'class="warn" style="font-size:16px"')}<span>Couldn’t check whether Tibia is running. Installing while it runs can lose the change: the game rewrites the file when it exits.</span>` : html`${icon('check-circle', 'class="ok" style="font-size:16px"')}Tibia is not running`}</div>
      ${p.tibia_unknown ? closedCheck(m) : ''}
      <div style="display:flex;gap:8px;align-items:flex-start">${icon('floppy-disk', 'class="accent" style="font-size:16px"')}<span>Backup first to <span class="mono">${p.backup_dir}</span></span></div>
      <div style="display:flex;gap:8px;align-items:center">${icon('arrows-counter-clockwise', 'class="accent" style="font-size:16px"')}The file is read back after writing. If it doesn’t match, the backup is restored.</div>
      ${p.limit.warn ? html`<div style="display:flex;gap:8px;align-items:center" class="warn">${icon('gauge', 'style="font-size:16px"')}${p.limit.text}</div>` : ''}
    </div>
    <div class="dialog-actions">
      <button class="btn btn-secondary" data-act="modal-close">Cancel</button>
      ${p.tibia_running ? html`<button class="btn btn-primary" data-act="install-preview">${icon('arrows-clockwise')}Check again</button>`
        : html`<button class="btn btn-primary" data-act="install-apply" ${raw(S.busy || (p.tibia_unknown && !m.closed) ? 'disabled' : '')} data-autofocus>Back up and install</button>`}
    </div></div>`;
}

const closedCheck = m => html`<label class="closed-check"><input type="checkbox" data-change="tibia-closed" ${raw(m.closed ? 'checked' : '')}>I’ve closed Tibia</label>`;

function viewRestoreModal(m) {
  const p = m.preview;
  return html`<div class="dialog" style="width:min(520px,100%)">
    <div class="dialog-title">Restore backup?</div>
    <div class="mono" style="font-size:12px;padding:10px 12px;border-radius:8px;background:var(--color-bg)">${p.file}</div>
    <div class="kv" style="grid-template-columns:120px 1fr">
      <span class="k">Character</span><span>${p.label || 'Unlabelled'} · <span class="mono">${p.folder}</span></span>
      <span class="k">Mode</span><span>${p.mode}</span>
      <span class="k">Accepted</span><span class="mono">${plural(p.accepted, 'item', 'items')}</span>
      <span class="k">Skipped</span><span class="mono">${plural(p.skipped, 'item', 'items')}</span>
    </div>
    <div style="font-size:12.5px;color:var(--muted)">The current file is backed up before restoring, so this can be undone too. Close Tibia first; the app won’t close it for you.</div>
    ${p.tibia_running ? html`<div style="font-size:12.5px" class="bad">Tibia is running. Close it yourself, then check again.</div>` : p.tibia_unknown ? html`<div style="font-size:12.5px" class="warn">Couldn’t check whether Tibia is running. Restoring while it runs can lose the change.</div>${closedCheck(m)}` : ''}
    <div class="dialog-actions"><button class="btn btn-secondary" data-act="modal-close">Cancel</button>
      ${p.tibia_running ? html`<button class="btn btn-primary" data-act="restore-recheck">${icon('arrows-clockwise')}Check again</button>`
        : html`<button class="btn btn-primary" data-act="restore-apply" ${raw(p.tibia_unknown && !m.closed ? 'disabled' : '')} data-autofocus>Restore</button>`}</div></div>`;
}

function viewReportModal(m) {
  const t = m.template, kind = (t.categories.find(c => c.key === m.category) || {}).kind;
  const field = (label, name, opts = {}) => html`<div class="field"><label>${label}</label>${opts.area
    ? html`<textarea class="input" data-input="rep-${name}" style="min-height:${opts.h || 64}px" placeholder="${opts.ph || ''}">${m.values[name] || ''}</textarea>`
    : html`<input class="input ${opts.mono ? 'mono' : ''}" data-input="rep-${name}" value="${m.values[name] || ''}" placeholder="${opts.ph || ''}">`}</div>`;
  return html`<div class="dialog" style="width:min(1040px,100%);gap:16px">
    <div style="display:flex;align-items:flex-start;gap:12px">
      <div style="flex:1"><div class="dialog-title">Report a problem</div><div style="font-size:13px;color:var(--muted);margin-top:2px">Nothing is sent until you choose to. Review and edit everything first.</div></div>
      <button class="btn btn-icon btn-ghost" data-act="report-close" title="Close" aria-label="Close" style="color:var(--color-text)">${icon('x', 'style="font-size:16px"')}</button>
    </div>
    <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px">
      <div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Category</label><select class="input" data-change="rep-category">${t.categories.map(c => html`<option value="${c.key}" ${raw(c.key === m.category ? 'selected' : '')}>${c.label}</option>`)}</select></div>
        ${field('Short title', 'title')}
        ${kind === 'item' ? html`
          <div style="display:grid;grid-template-columns:1fr 130px;gap:10px">${field('Item name', 'item_name')}${field('Tibia item ID', 'item_id', { mono: true })}</div>
          ${field('Source URL', 'source_url')}
          ${field('Expected or corrected value', 'expected', { ph: 'e.g. the ID shown in the in-game Cyclopedia' })}` : ''}
        ${kind === 'bug' ? html`${field('Steps to reproduce', 'steps', { area: true, ph: '1. Open Install to character…' })}
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${field('Expected behavior', 'expected_behavior')}${field('Actual behavior', 'actual_behavior')}</div>` : ''}
        ${field('Description', 'description', { area: true, ph: 'What is wrong, and how do you know?' })}
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;cursor:pointer"><input type="checkbox" data-change="rep-diag" ${raw(m.includeDiag ? 'checked' : '')}>Include diagnostic information (you can edit it)</label>
        ${m.includeDiag ? html`<textarea class="input mono" data-input="rep-diagnostics" style="min-height:120px;font-size:12px">${m.diagnostics}</textarea>` : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;min-width:0">
        <div style="display:flex;align-items:center;gap:8px"><span class="kicker" style="flex:1">Report preview</span>
          ${m.previewEdited ? html`<button class="btn btn-ghost" data-act="report-regen" style="font-size:12.5px;padding-inline:8px">${icon('arrows-clockwise')}Rebuild from the form</button>` : ''}</div>
        <textarea class="input mono" data-input="rep-preview" aria-label="Report preview" style="flex:1;min-height:320px;padding:14px;background:var(--color-bg);box-shadow:var(--shadow-sm);font-size:12px;line-height:1.65">${m.preview}</textarea>
        <div style="display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:var(--muted)">${icon('shield-check', 'class="accent" style="font-size:16px;flex:none;margin-top:1px"')}<span>User folders, character folder numbers, character labels and e-mail addresses are removed from diagnostics. Loot files and character data are never attached. Don’t add passwords or account details.</span></div>
      </div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="flex:1;min-width:240px;font-size:12.5px;color:var(--muted)">${t.destinations.url || t.destinations.email ? 'Sending opens your browser or e-mail program with the report filled in; you submit it there.' : 'No support destination is configured, so this report can’t be sent from here. Copy it or save it to a file.'}</span>
      <button class="btn btn-secondary" data-act="report-close">Close</button>
      <button class="btn btn-secondary" data-act="report-save">${icon('floppy-disk')}Save to file…</button>
      <button class="btn ${t.destinations.url || t.destinations.email ? 'btn-secondary' : 'btn-primary'}" data-act="report-copy">${icon(m.copied ? 'check' : 'copy')}${m.copied ? 'Copied' : 'Copy report'}</button>
      ${t.destinations.url ? html`<button class="btn btn-primary" data-act="report-send" data-v="0">${icon('paper-plane-tilt')}Open report page…</button>` : ''}
      ${t.destinations.email ? html`<button class="btn btn-primary" data-act="report-send" data-v="${t.destinations.url ? 1 : 0}">${icon('envelope-simple')}Send by e-mail…</button>` : ''}
    </div></div>`;
}

// ---------- actions -----------------------------------------------------------------

async function openReport(category, key, operation, error) {
  await guard(async () => {
    const t = await get('report/template', { category, key, operation, error });
    S.modal = { kind: 'report', template: t, category: t.category, includeDiag: true, diagnostics: t.diagnostics,
      values: { title: t.title, item_name: t.item_name, item_id: t.item_id, source_url: t.source_url },
      preview: '', previewEdited: false, handled: false, copied: false, dirty: false };
    await composeReport();
    render();
  });
}

async function composeReport() {
  const m = S.modal;
  if (!m || m.kind !== 'report' || m.previewEdited) return;
  const r = await post('report/compose', { category: m.category, title: m.values.title || '', values: m.values,
    diagnostics: m.includeDiag ? m.diagnostics : null });
  m.preview = r.title + '\n\n' + r.body;
}

function splitReport(text) {
  const i = text.indexOf('\n');
  return i < 0 ? { title: text.trim(), body: '' } : { title: text.slice(0, i).trim(), body: text.slice(i + 1).trim() + '\n' };
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy'); ta.remove(); return ok; }
}

async function pollUpdate() {
  const st = await get('update/status');
  if (!S.modal || S.modal.kind !== 'update') return;
  Object.assign(S.modal, { running: st.running, progress: st.progress, review: st.review });
  if (st.error) { S.modal = null; showError({ user: true, message: st.error }, 'Check for updates'); return; }
  render();
  if (st.running) setTimeout(() => guard(pollUpdate, 'Check for updates'), 600);
}

async function refreshProfilesModal() {
  if (!S.modal || S.modal.kind !== 'profiles') { render(); return; }
  await guard(async () => { S.modal.data = await get('profiles'); S.modal.renaming = null; });
  render();
}

async function switchProfile(id) {
  await guard(async () => { await post('profiles/switch', { id }); await loadState(); await LOADERS[S.screen](); toast(`Switched to “${S.app.profile.name}”.`); }, 'Switch profile');
  render();
}

async function previewInstall() {
  S.busy = true;
  await guard(async () => {
    const preview = await post('install/preview', { folder: S.ins.selected, mode: S.ins.mode });
    S.modal = { kind: 'install', preview };
  }, 'Preview installation');
  S.busy = false;
  render();
}

const ACTIONS = {
  'go': el => go(el.dataset.screen),
  'theme': async () => { const t = S.app.theme === 'dark' ? 'light' : 'dark'; await post('theme', { theme: t }); S.app.theme = t; $app().dataset.theme = t; render(); },
  'notices-dismiss': async () => { await post('notices/dismiss'); S.app.notices = []; render(); },
  'open-url': el => guard(() => post('open-url', { url: el.dataset.url })),
  'open-folder': el => guard(() => post('open-folder', { which: el.dataset.which, folder: el.dataset.folder })),
  'contact': () => guard(async () => { const h = S.help.data; if (h.support.contact) await post('open-url', { url: h.support.contact }); }),
  'show-item': async el => { S.cat.sel = el.dataset.key; S.screen = 'catalog'; location.hash = 'catalog'; await guard(async () => { await loadCatalog(false); await loadItem(el.dataset.key); }); render(true); },

  // onboarding
  'onb-next': async () => {
    const o = S.onb;
    if (o.step < 3) { o.step += 1; await guard(async () => { o.info = await get('onboarding'); }); render(); return; }
    const done = await guard(async () => { await post('onboarding/finish', { start_full: o.start !== 'empty', level: o.start === 'level' ? o.level : '' }); await loadState(); return true; }, 'Finish setup');
    if (done) await go('accepted');
  },
  'onb-back': () => { S.onb.step = Math.max(1, S.onb.step - 1); render(); },
  'onb-start': (el, e) => { if (e && e.target.closest('select')) return; S.onb.start = el.dataset.v; render(); },

  // catalog
  'cat-seg': async el => { S.cat.seg = el.dataset.v; await guard(() => loadCatalog()); render(); },
  'cat-idf': async el => { S.cat.idf = el.dataset.v; await guard(() => loadCatalog()); render(); },
  'cat-more': async () => {
    const c = S.cat;
    if (c.loadingMore) return;
    c.loadingMore = true; render();
    await guard(async () => {
      const r = await get('catalog', { q: c.q, seg: c.seg, cat: c.cat, idf: c.idf, offset: c.rows.length, limit: PAGE });
      const seen = new Set(c.rows.map(x => x.key));
      c.rows = c.rows.concat(r.rows.filter(x => !seen.has(x.key))); c.total = r.total;
    }, 'Load more catalog items');
    c.loadingMore = false; render();
  },
  'cat-select': async el => { await guard(() => loadItem(el.dataset.key)); render(); },
  'cat-toggle': async el => { await guard(async () => { await post('accepted/toggle', { key: el.dataset.key }); await loadState(); await loadCatalog(false); }); render(); },
  'detail-fav': async () => { await guard(async () => { await post('favorites/toggle', { key: S.cat.sel }); await loadCatalog(false); }); render(); },
  'search-apply': async el => {
    const s = (S.app.saved_searches || [])[+el.dataset.v];
    if (!s) return;
    Object.assign(S.cat, { q: s.q, seg: s.seg, cat: s.cat, idf: s.idf, sel: null });
    await guard(() => loadCatalog()); render(true);
  },
  'search-delete': async el => { await guard(async () => { await post('searches/delete', { name: el.dataset.v }); await loadState(); }); render(); },
  'search-save-open': () => {
    const c = S.cat;
    S.modal = { kind: 'search-save', name: c.q.trim() || c.cat || { del: 'Delivery Task', mine: 'In my list', fav: 'Favorites' }[c.seg] || '' };
    render();
  },
  'search-save': async () => {
    await guard(async () => { await post('searches/save', { name: S.modal.name, ...searchOf(S.cat) }); S.modal = null; await loadState(); }, 'Save search');
    render();
  },
  'bulk': async el => {
    const add = el.dataset.v === 'add';
    await guard(async () => {
      const r = await post('accepted/bulk', { ...searchOf(S.cat), add, dry_run: true });
      if (!r.changes) { toast(add ? 'All of these are already on your list.' : 'None of these are on your list.', 'info'); return; }
      const extra = add && r.unverified ? ` ${plural(r.unverified, 'of them has', 'of them have')} an unverified item ID and won’t be exported until it’s verified.` : '';
      S.modal = {
        kind: 'confirm', title: add ? `Add ${plural(r.changes, 'item', 'items')}?` : `Remove ${plural(r.changes, 'item', 'items')}?`,
        body: (add ? `Adds every item matching this search that isn’t on “${(S.app.profile || {}).name}” yet.` : `Removes every item matching this search from “${(S.app.profile || {}).name}”.`)
          + `${r.matching !== r.changes ? ` ${num(r.matching - r.changes)} of the ${num(r.matching)} matching items ${add ? 'are already on it' : 'aren’t on it'}.` : ''}${extra} You can undo this from the profile’s history.`,
        ok: add ? 'Add all' : 'Remove all',
        run: async () => {
          const done = await post('accepted/bulk', { ...searchOf(S.cat), add });
          await loadState(); await loadCatalog(false);
          toast(`${add ? 'Added' : 'Removed'} ${plural(done.changed, 'item', 'items')}.`);
        },
      };
    }, add ? 'Add all to Accepted Loot' : 'Remove all from Accepted Loot');
    render();
  },
  'detail-acc': async () => { await guard(async () => { await post('accepted/toggle', { key: S.cat.sel }); await loadState(); await loadCatalog(false); }); render(); },
  'detail-del': async () => { await guard(async () => { await post('delivery/toggle', { key: S.cat.sel }); await loadState(); await loadCatalog(false); }); render(); },
  'detail-lookup': async () => {
    S.cat.lookup = true; render();
    await guard(async () => { S.cat.item = await post('item/lookup', { key: S.cat.sel }); }, 'Look up drops on TibiaWiki');
    S.cat.lookup = false; render();
  },

  // delivery
  'del-tab': async el => { S.del.tab = el.dataset.v; await guard(LOADERS.delivery); render(); },
  'del-toggle': async el => { await guard(async () => { await post('delivery/toggle', { key: el.dataset.key }); await loadState(); await LOADERS.delivery(); }); render(); },
  'del-defaults': () => { S.modal = { kind: 'confirm', title: 'Restore source defaults?', body: 'Discard your Delivery Task list edits (exclusions and additions) and use the source list as it is. Your Accepted Loot edits are not affected.', ok: 'Restore defaults',
    run: async () => { await post('delivery/defaults'); await loadState(); await LOADERS.delivery(); } }; render(); },

  // accepted
  'acc-tab': async el => { S.acc.tab = el.dataset.v; await guard(LOADERS.accepted); render(); },
  'acc-toggle': async el => { await guard(async () => { await post('accepted/toggle', { key: el.dataset.key }); await loadState(); await LOADERS.accepted(); }); render(); },
  'level-pick': async el => {
    const level = el.dataset.v;
    if (level === S.acc.data.levels.current) return;
    S.modal = { kind: 'level', level, preview: null }; render();
    await guard(async () => { S.modal.preview = await get('strictness/preview', { level }); }, 'Preview strictness level');
    render();
  },
  'level-apply': async () => {
    const p = S.modal.preview;
    await guard(async () => {
      const r = await post('strictness/apply', { level: p.level });
      S.modal = null; await loadState(); await LOADERS.accepted();
      toast(p.level ? `Using the ${p.name} level: ${plural(r.count, 'item', 'items')} on your list.` : 'Stopped using a strictness level.');
    }, 'Use strictness level');
    render();
  },
  'level-review': () => {
    const d = S.acc.data;
    S.modal = { kind: 'level', review: true, name: d.level.name, preview: { ...d.level.pending, level: d.level.id, name: d.level.name } };
    render();
  },
  'level-accept': async () => {
    await guard(async () => { await post('strictness/accept-changes'); S.modal = null; await loadState(); await LOADERS.accepted(); toast('Level updated.'); }, 'Update strictness level');
    render();
  },
  'acc-follow': async () => { await guard(async () => { await post('accepted/follow', { on: !S.acc.data.follow_delivery }); await loadState(); await LOADERS.accepted(); }); render(); },
  'acc-defaults': () => { S.modal = { kind: 'confirm', title: 'Restore defaults?', body: 'Reset your Accepted Loot list to exactly your Delivery Task list. This discards items you added directly and items you removed. Your Delivery Task list edits are kept.', ok: 'Restore defaults',
    run: async () => { await post('accepted/defaults'); await loadState(); await LOADERS.accepted(); } }; render(); },

  // export
  'exp-copy': async () => { if (await copyText(S.exp.data.lines.join('\n'))) { S.exp.copied = true; render(); setTimeout(() => { S.exp.copied = false; if (S.screen === 'export') render(); }, 1600); } },
  'exp-save-text': () => guard(async () => { const r = await post('export/text', { sort: S.exp.sort, ids: S.exp.ids }); if (r.path) toast('Saved ' + r.path); }, 'Save list as text'),
  'exp-file': () => guard(async () => { const r = await post('export/file'); if (r.path) toast(`Exported ${num(r.count)} items to ${r.path}`); }, 'Export game file'),

  // install
  'folder-browse': async () => {
    await guard(async () => {
      const r = await post('install/folder', {});
      if (r.cancelled) return;
      S.ins.selected = null;
      if (!S.app.onboarded) S.onb.info = await get('onboarding'); else await LOADERS[S.screen]();
      await loadState();
      toast('Tibia folder set. Press Check for updates to reload the item catalog from this client if needed.', 'info');
    }, 'Choose Tibia folder');
    render();
  },
  'folder-default': async () => { await guard(async () => { await post('install/folder', { default: true }); S.ins.selected = null; await LOADERS.install(); }); render(); },
  'ins-select': async (el, e) => { if (e.target.closest('[data-act="ins-edit"],input')) return; S.ins.selected = el.dataset.v; await guard(LOADERS.install); render(); },
  'ins-edit': el => { S.ins.editing = el.dataset.v; render(); },
  'ins-mode': el => { S.ins.mode = el.dataset.v; render(); },
  'install-preview': () => previewInstall(),
  'install-apply': async () => {
    S.busy = true; render();
    try {
      const r = await post('install/apply', { folder: S.ins.selected, mode: S.ins.mode, closed: !!S.modal.closed });
      S.modal = null;
      toast(`Installed and verified ${num(r.count)} Accepted Loot items.` + (r.backup ? ` Backup: ${r.backup}` : ' (No previous file to back up.)'));
      await LOADERS.install();
    } catch (e) { S.busy = false; showError(e, `Install loot list (${S.ins.mode})`); return; }
    S.busy = false; render();
  },
  'restore-open': el => guard(async () => { const preview = await post('restore/preview', { folder: S.ins.selected, file: el.dataset.file }); S.modal = { kind: 'restore', preview }; render(); }, 'Preview backup restore'),
  'restore-recheck': () => guard(async () => { const p = S.modal.preview; S.modal = { kind: 'restore', preview: await post('restore/preview', { folder: p.folder, file: p.file }) }; render(); }, 'Check whether Tibia is running'),
  'restore-apply': async () => {
    const p = S.modal.preview;
    try { await post('restore/apply', { folder: p.folder, file: p.file, closed: !!S.modal.closed }); S.modal = null; toast('The backup was restored and verified.'); await LOADERS.install(); render(); }
    catch (e) { showError(e, 'Restore loot-list backup'); }
  },

  // sources
  'limit-save': async () => { const v = document.querySelector('[data-input="limit"]').value.trim(); await guard(async () => { await post('settings/limit', { limit: v }); await loadState(); await LOADERS.sources(); toast('Limit saved.'); }); render(); },
  'update-start': async () => { S.modal = { kind: 'update', running: true, progress: 'Starting…', tab: 'new', keep: false }; render(); await guard(async () => { await post('update/start'); await pollUpdate(); }, 'Check for updates'); },
  'update-tab': el => { S.modal.tab = el.dataset.v; render(); },
  'update-cancel': async () => { await post('update/cancel').catch(() => {}); S.modal = null; await refresh(); },
  'update-apply': async () => {
    await guard(async () => { await post('update/apply', { keep_removed: !!S.modal.keep }); S.modal = null; await loadState(); await LOADERS[S.screen](); toast('Updates applied.'); }, 'Apply updates');
    render();
  },

  // help & reports
  'faq': el => { const i = Number(el.dataset.v); S.help.open = S.help.open === i ? -1 : i; render(); },
  'report-open': el => openReport(el.dataset.category, el.dataset.key),
  'error-report': () => { const m = S.modal; openReport(m.bug ? 'bug' : 'install', null, m.operation || '', m.message); },
  'report-regen': async () => { S.modal.previewEdited = false; await guard(composeReport); render(); },
  'report-copy': async () => { const m = S.modal; if (await copyText(m.preview)) { m.copied = true; m.handled = true; render(); setTimeout(() => { if (S.modal === m) { m.copied = false; render(); } }, 1600); } },
  'report-save': () => guard(async () => { const { title, body } = splitReport(S.modal.preview); const r = await post('report/save', { title, body }); if (r.path) { S.modal.handled = true; toast('Report saved to ' + r.path); } }, 'Save report'),
  'report-send': el => guard(async () => {
    const m = S.modal, { title, body } = splitReport(m.preview);
    const { links } = await post('report/links', { title, body });
    const link = links[Number(el.dataset.v || 0)];
    if (!link) return;
    if (link.needs_clipboard) { await copyText(m.preview); toast('The report is long, so it was copied to your clipboard. Paste it into the page that opens.', 'info'); }
    await post('open-url', { url: link.url });
    m.handled = true;
  }, 'Send report'),
  'report-close': async () => {
    const m = S.modal;
    if (!m.handled && m.dirty) {
      S.modal = { kind: 'confirm', title: 'Keep this report?', body: 'This report has not been copied, saved or sent. Save a copy in the app’s reports folder before closing?', ok: 'Save and close',
        cancelLabel: 'Discard', run: async () => { const { title, body } = splitReport(m.preview); const r = await post('report/save', { title, body, choose: false }); toast('Report saved to ' + r.path); } };
      render(); return;
    }
    S.modal = null; render();
  },

  // profiles
  'profile-new': () => { S.modal = { kind: 'profile-new', name: '', start: 'delivery' }; render(); },
  'profile-start': el => { S.modal.start = el.dataset.v; render(); },
  'profile-create': async () => {
    const m = S.modal;
    await guard(async () => {
      await post('profiles/create', { name: m.name, start: m.start });
      S.modal = null; await loadState(); await LOADERS.accepted(); toast('Switched to the new profile.');
    }, 'Create profile');
    render();
  },
  'profiles-open': async () => { S.modal = { kind: 'profiles', data: null }; render(); await refreshProfilesModal(); },
  'profile-switch-to': async el => { await switchProfile(el.dataset.v); await refreshProfilesModal(); },
  'profile-rename': el => { S.modal.renaming = el.dataset.v; render(); },
  'profile-duplicate': async el => { await guard(() => post('profiles/duplicate', { id: el.dataset.v }), 'Duplicate profile'); await refreshProfilesModal(); },
  'profile-export': el => guard(async () => { const r = await post('profiles/export', { id: el.dataset.v }); if (r.path) toast('Profile saved to ' + r.path); }, 'Export profile'),
  'profile-delete': el => {
    const p = S.modal.data.profiles.find(x => x.id === el.dataset.v), back = S.modal;
    S.modal = { kind: 'confirm', title: `Delete “${p.name}”?`, body: `Its list (${num(p.count)} items) and history are removed. Export it first if you may want it back.`, ok: 'Delete profile',
      run: async () => { await post('profiles/delete', { id: p.id }); await loadState(); await LOADERS[S.screen](); S.modal = back; await refreshProfilesModal(); } };
    render();
  },
  'profile-import': async () => {
    await guard(async () => { const r = await post('profiles/import'); if (r.cancelled) return; await loadState(); await LOADERS[S.screen](); toast(`Imported “${r.name}” and switched to it.`); }, 'Import profile');
    await refreshProfilesModal();
  },
  'profile-from-char': async () => {
    const m = S.modal, folder = m.char || (m.data.characters[0] || {}).id;
    await guard(async () => { const r = await post('profiles/from-character', { folder }); await loadState(); await LOADERS[S.screen](); toast(`New profile with the character’s ${num(r.count)} Accepted items.`); }, 'Create profile from character');
    await refreshProfilesModal();
  },
  'profile-compare': async () => {
    const m = S.modal, ids = m.data.profiles.map(p => p.id);
    await guard(async () => { m.cmp = await get('profiles/compare', { a: m.cmpA || ids[0], b: m.cmpB || ids[1] || ids[0] }); }, 'Compare profiles');
    render();
  },
  'history-open': async () => {
    S.modal = { kind: 'history', data: null }; render();
    await guard(async () => { S.modal.data = await get('profiles/history', { id: S.app.profile.id }); }, 'Show history');
    render();
  },
  'history-restore': async el => {
    await guard(async () => {
      await post('profiles/restore', { id: S.app.profile.id, index: Number(el.dataset.v) });
      await loadState(); await LOADERS[S.screen]();
      S.modal.data = await get('profiles/history', { id: S.app.profile.id });
      toast('Earlier version restored.');
    }, 'Restore profile version');
    render();
  },

  // weekly tasks
  'week-pick': el => { const r = S.week.results.find(x => x.key === el.dataset.key); S.week.pick = r; if (!S.week.required) S.week.required = String(r.min || ''); render(); },
  'week-add': async () => {
    const w = S.week;
    await guard(async () => { await post('weekly/add', { key: w.pick.key, required: w.required || null }); w.pick = null; w.q = ''; w.results = []; w.required = ''; await LOADERS.weekly(); }, 'Add weekly task');
    render();
  },
  'week-step': async el => { await guard(async () => { await post('weekly/set', { key: el.dataset.key, delta: Number(el.dataset.v) }); await LOADERS.weekly(); }, 'Update task'); render(); },
  'week-remove': async el => { await guard(async () => { await post('weekly/remove', { key: el.dataset.key }); await LOADERS.weekly(); }, 'Remove task'); render(); },
  'week-add-missing': async () => {
    await guard(async () => { const r = await post('weekly/add-missing'); await loadState(); await LOADERS.weekly(); toast(`Added ${r.added.join(', ')} to your Accepted Loot list.`); }, 'Add task items');
    render();
  },

  // hunt reports
  'hunt-paste': async () => {
    try { S.hunt.text = await navigator.clipboard.readText(); render(); }
    catch { toast('Couldn’t read the clipboard here. Click in the box and press Ctrl+V instead.', 'info'); }
  },
  'hunt-analyze': async () => { await guard(async () => { S.hunt.analysis = await post('hunts/analyze', { text: S.hunt.text }); await LOADERS.hunts(); }, 'Analyze hunt report'); render(); },
  'hunt-open': async el => { await guard(async () => { S.hunt.analysis = await get('hunts/open', { id: el.dataset.v }); S.hunt.text = ''; }, 'Open hunt report'); render(true); },
  'hunt-delete': async el => {
    await guard(async () => { await post('hunts/delete', { id: el.dataset.v }); if (S.hunt.analysis && S.hunt.analysis.id === el.dataset.v) S.hunt.analysis = null; await LOADERS.hunts(); }, 'Delete hunt report');
    render();
  },
  'hunt-apply': async () => {
    await guard(async () => {
      const r = await post('hunts/apply-tasks', { id: S.hunt.analysis.id });
      S.hunt.analysis = await get('hunts/open', { id: S.hunt.analysis.id });
      toast('Added to Weekly Tasks: ' + r.updated.map(u => `${u.name} +${num(u.added)}`).join(', '));
    }, 'Add hunt loot to Weekly Tasks');
    render();
  },
  'hunt-accept': async el => {
    await guard(async () => { await post('accepted/toggle', { key: el.dataset.key }); await loadState(); S.hunt.analysis = await get('hunts/open', { id: S.hunt.analysis.id }); }, 'Add to Accepted Loot');
    render();
  },

  // generic modal
  'modal-close': () => { S.modal = null; render(); },
  'confirm-ok': async () => { const m = S.modal; S.modal = null; await guard(m.run); render(); },
};

const CHANGES = {
  'week-collected': async el => { await guard(async () => { await post('weekly/set', { key: el.dataset.key, collected: el.value }); }, 'Update task'); await guard(LOADERS.weekly); render(); },
  'week-required': async el => { await guard(async () => { await post('weekly/set', { key: el.dataset.key, required: el.value }); }, 'Update task'); await guard(LOADERS.weekly); render(); },
  'profile-switch': el => switchProfile(el.value),
  'profile-char': el => { S.modal.char = el.value; },
  'profile-cmp-a': el => { S.modal.cmpA = el.value; },
  'profile-cmp-b': el => { S.modal.cmpB = el.value; },
  'onb-level': el => { S.onb.level = el.value; },
  'tibia-closed': el => { S.modal.closed = el.checked; render(); },
  'hunt-choose': async el => {
    if (!el.value) return;
    await guard(async () => { await post('hunts/choose', { line: el.dataset.line, id: +el.value }); S.hunt.analysis = await get('hunts/open', { id: S.hunt.analysis.id }); }, 'Pick the looted item');
    render();
  },
  'cat-cat': async el => { S.cat.cat = el.value; await guard(() => loadCatalog()); render(); },
  'exp-sort': async el => { S.exp.sort = el.value; await guard(LOADERS.export); render(); },
  'exp-ids': async el => { S.exp.ids = el.checked; await guard(LOADERS.export); render(); },
  'update-keep': el => { S.modal.keep = el.checked; },
  'rep-category': async el => { S.modal.category = el.value; S.modal.dirty = true; await guard(composeReport); render(); },
  'rep-diag': async el => { S.modal.includeDiag = el.checked; await guard(composeReport); render(); },
};

let searchTimer = null, composeTimer = null;
let weekTimer = null;
const INPUTS = {
  'hunt-text': el => {
    const had = !!S.hunt.text.trim(); S.hunt.text = el.value;
    const btn = document.querySelector('[data-act="hunt-analyze"]');
    if (btn && had !== !!el.value.trim()) btn.disabled = !el.value.trim();
  },
  'weekly-required': el => { S.week.required = el.value; },
  'weekly-search': el => {
    S.week.q = el.value; S.week.pick = null;
    clearTimeout(weekTimer);
    weekTimer = setTimeout(async () => {
      await guard(async () => { S.week.results = S.week.q.trim() ? (await get('weekly/search', { q: S.week.q })).rows : []; });
      const box = document.getElementById('week-results'); if (box) box.innerHTML = val(viewWeeklyResults());
    }, 200);
  },
  'profile-name': el => { S.modal.name = el.value; },
  'search-name': el => { S.modal.name = el.value; },
  'search': el => {
    S.cat.q = el.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      await guard(() => loadCatalog());
      const list = document.getElementById('cat-list');
      // Redraw the parts that depend on the search, but not the search box (it keeps focus while typing).
      if (list && S.screen === 'catalog') { list.innerHTML = val(viewCatalogRows()); list.scrollTop = 0;
        const aside = document.querySelector('.detail'); if (aside) aside.outerHTML = val(viewDetail());
        const tools = document.getElementById('cat-tools'); if (tools) tools.outerHTML = val(viewCatalogTools());
        const count = document.getElementById('cat-count'); if (count) count.textContent = catCount(S.cat); }
    }, 220);
  },
  'rep-preview': el => { S.modal.preview = el.value; S.modal.previewEdited = true; S.modal.dirty = true;
    if (!document.querySelector('[data-act="report-regen"]')) { const head = el.previousElementSibling; head.insertAdjacentHTML('beforeend', val(html`<button class="btn btn-ghost" data-act="report-regen" style="font-size:12.5px;padding-inline:8px">${icon('arrows-clockwise')}Rebuild from the form</button>`)); } },
};

function onReportField(el) {
  const m = S.modal, name = el.dataset.input.slice(4);
  if (name === 'diagnostics') m.diagnostics = el.value; else m.values[name] = el.value;
  m.dirty = true;
  if (m.previewEdited) return;
  clearTimeout(composeTimer);
  composeTimer = setTimeout(async () => {
    await guard(composeReport);
    const pv = document.querySelector('[data-input="rep-preview"]');
    if (pv && S.modal === m) pv.value = m.preview;
  }, 250);
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  if (el.tagName === 'A') e.preventDefault();
  const fn = ACTIONS[el.dataset.act];
  if (fn) { e.stopPropagation(); fn(el, e); }
});
document.addEventListener('change', e => {
  const el = e.target;
  if (el.dataset && el.dataset.change && CHANGES[el.dataset.change]) CHANGES[el.dataset.change](el);
});
document.addEventListener('input', e => {
  const el = e.target, key = el.dataset && el.dataset.input;
  if (!key) return;
  if (INPUTS[key]) INPUTS[key](el);
  else if (key.startsWith('rep-')) onReportField(el);
});
document.addEventListener('keydown', e => {
  const el = e.target;
  if (e.key === 'Escape' && S.modal && !(S.modal.kind === 'update' && S.modal.running)) {
    if (S.modal.kind === 'report') ACTIONS['report-close'](); else { S.modal = null; render(); }
    return;
  }
  if (el.dataset && el.dataset.input === 'profile-rename') {
    if (e.key === 'Enter') saveRename(el);
    else if (e.key === 'Escape') { S.modal.renaming = null; render(); }
    return;
  }
  if (el.dataset && el.dataset.input === 'profile-name' && e.key === 'Enter') { ACTIONS['profile-create'](); return; }
  if (el.dataset && el.dataset.input === 'search-name' && e.key === 'Enter') { ACTIONS['search-save'](); return; }
  if (el.dataset && el.dataset.input === 'label') {
    if (e.key === 'Enter') { guard(async () => { await post('install/label', { folder: el.dataset.folder, label: el.value }); S.ins.editing = null; await LOADERS.install(); render(); }); }
    else if (e.key === 'Escape') { S.ins.editing = null; render(); }
    return;
  }
  if (el.dataset && el.dataset.input === 'limit' && e.key === 'Enter') { ACTIONS['limit-save'](); return; }
  if ((e.key === 'Enter' || e.key === ' ') && el.matches('[role="radio"],[role="switch"]') && el.dataset.act) { e.preventDefault(); el.click(); }
});
async function saveRename(el) {
  if (!S.modal || S.modal.renaming !== el.dataset.id) return;
  S.modal.renaming = null;
  await guard(() => post('profiles/rename', { id: el.dataset.id, name: el.value }), 'Rename profile');
  await loadState(); if (S.screen === 'accepted') await LOADERS.accepted();
  await refreshProfilesModal();
}

document.addEventListener('focusout', e => {
  const el = e.target;
  if (el.dataset && el.dataset.input === 'profile-rename') { saveRename(el); return; }
  if (el.dataset && el.dataset.input === 'label' && S.ins.editing === el.dataset.folder) {
    guard(async () => { await post('install/label', { folder: el.dataset.folder, label: el.value }); S.ins.editing = null; await LOADERS.install(); render(); });
  }
});

document.addEventListener('error', e => {
  if (e.target && e.target.tagName === 'IMG') { e.target.remove(); }
}, true);

// keep the app alive while this window is open
setInterval(() => post('ping').catch(() => {}), 5000);

// ---------- start -------------------------------------------------------------------

(async function start() {
  try {
    await loadState();
    post('ping').catch(() => {});
    if (!S.app.onboarded) { S.onb.info = await get('onboarding'); render(); return; }
    const want = location.hash.replace('#', '');
    await go(SCREENS.some(([k]) => k === want) ? want : 'catalog');
  } catch (e) {
    $app().innerHTML = val(html`<div style="margin:auto;max-width:520px;padding:24px;text-align:center"><div class="dialog-title">Couldn’t start</div><p class="muted">${e.message}</p><p class="muted" style="font-size:13px">Close this window and start the app again.</p></div>`);
  }
})();
