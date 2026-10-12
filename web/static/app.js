'use strict';
/* Internship Tracker front end. No frameworks, no CDN: plain DOM built with h(); all text goes in as
   text nodes, never as HTML, so nothing from a job posting can inject markup. */

// ------------------------------------------------------------------ helpers

const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = 'http://www.w3.org/2000/svg';

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style') Object.assign(el.style, v);
    else if (k === 'value') el.value = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

/** replaceChildren that accepts nested arrays and skips null/false. */
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
  return el;
}

const ICONS = {
  search: ['M11 3a8 8 0 1 0 0 16a8 8 0 0 0 0-16Z', 'm21 21-4.3-4.3'],
  ai: ['M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9Z', 'M19 17v4', 'M17 19h4'],
  flag: ['M4 22V4', 'M4 4h12l-2 4 2 4H4'],
  moon: ['M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z'],
  alert: ['M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z', 'M12 9v4', 'M12 17h.01'],
  check: ['M20 6 9 17l-5-5'],
  x: ['M18 6 6 18', 'M6 6l12 12'],
  minus: ['M6 12h12'],
  info: ['M12 2a10 10 0 1 0 0 20a10 10 0 0 0 0-20Z', 'M12 16v-4', 'M12 8h.01'],
  dot: ['M12 12h.01'],
  ext: ['M15 3h6v6', 'M10 14 21 3', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'],
  lock: ['M7 11V7a5 5 0 0 1 10 0v4', 'M5 11h14v10H5Z'],
  play: ['M6 4l14 8-14 8Z'],
};
function icon(name, cls) {
  return s('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true', class: cls || 'i' }, (ICONS[name] || []).map((d) => s('path', { d })));
}

async function api(method, path, body, opts = {}) {
  const init = { method, headers: {}, ...opts };
  if (method !== 'GET') {
    init.headers['X-Tracker'] = '1';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body ?? {});
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (res.status === 401) {
    location.href = '/login';
    throw new Error('Sign in required');
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (HTTP ${res.status})`);
  return data;
}

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem('tracker:' + key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('tracker:' + key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso);
  return isNaN(d) ? null : d;
}
function fmtDate(iso, withYear = false) {
  const d = parseDate(iso);
  if (!d) return '';
  const opts = { month: 'short', day: 'numeric' };
  if (withYear || d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString(undefined, opts);
}
function fmtTime(iso) {
  const d = parseDate(iso);
  return d ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '';
}
function ago(iso) {
  const d = parseDate(iso);
  if (!d) return 'never';
  const sec = Math.round((Date.now() - d) / 1000);
  if (sec < 45) return 'just now';
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  const days = Math.round(sec / 86400);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}
function duration(a, b) {
  const s1 = parseDate(a), s2 = b ? parseDate(b) : new Date();
  if (!s1 || !s2) return '';
  const sec = Math.max(0, Math.round((s2 - s1) / 1000));
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m ${sec % 60}s`;
}
const SOURCE_LABELS = {
  greenhouse: 'Greenhouse', lever: 'Lever', ashby: 'Ashby', workable: 'Workable',
  internshala: 'Internshala', unstop: 'Unstop', ycombinator: 'Y Combinator jobs', vcboards: 'VC portfolio boards',
  peerlist: 'Peerlist', himalayas: 'Himalayas', remoteok: 'Remote OK', remotive: 'Remotive',
  weworkremotely: 'We Work Remotely', hackernews: 'Hacker News', simplify: 'SimplifyJobs (GitHub)',
  speedyapply: 'speedyapply (GitHub)', manual: 'Added by you', demo: 'sample',
};
const FEEDS = new Set(['internshala', 'unstop', 'ycombinator', 'vcboards', 'peerlist', 'himalayas', 'remoteok', 'remotive',
  'weworkremotely', 'hackernews', 'simplify', 'speedyapply']);
const sourceLabel = (s) => SOURCE_LABELS[s] || s || '';
const jobOrigin = (j) => (j.source === 'manual'
  ? `added by you${j.platform ? ` from ${j.platform}` : ''}`
  : `${FEEDS.has(j.source) ? 'via ' : ''}${(FEEDS.has(j.source) && j.platform) || sourceLabel(j.source)}`);
const todayIso = () => new Date().toLocaleDateString('en-CA');
const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || `${word}s`)}`;

let toastTimer;
function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, isError ? 5000 : 2600);
}

function showTip(x, y, ...content) {
  const tip = $('#tooltip');
  fill(tip, ...content);
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y - r.height - 10;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top < 8) top = y + 16;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
function hideTip() { $('#tooltip').hidden = true; }

// ------------------------------------------------------------------ state & routing

const S = {
  meta: null,
  jobs: [],
  status: null,
  jobsVersion: null,
  view: null,
  param: null,
  drawerId: null,
  pending: {},          // unsaved drawer edits: { field: value }
  saveTimer: null,
  dirtyView: null,      // view name with unsaved edits (companies/settings)
  filters: Object.assign(
    { q: '', statuses: [], minScore: 0, hideClosed: false, hideSkipped: false, flag: '', source: '', sort: 'found', dir: 'desc' },
    store.get('filters', {}),
  ),
  renderToken: 0,
};

const VIEWS = {
  applications: renderApplications,
  dashboard: renderDashboard,
  platforms: renderPlatforms,
  activity: renderActivity,
  companies: renderCompanies,
  settings: renderSettings,
};

function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }

function route() {
  const m = location.hash.match(/^#\/?([a-z]+)?(?:\/(\d+))?/) || [];
  const view = VIEWS[m[1]] ? m[1] : 'applications';
  const param = m[2] ? Number(m[2]) : null;

  if (S.dirtyView && S.dirtyView !== view) {
    if (!confirm('You have unsaved changes on this page. Leave without saving?')) {
      history.replaceState(null, '', `#/${S.dirtyView}`);
      return;
    }
    S.dirtyView = null;
  }
  document.querySelectorAll('.tabs a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));

  const viewChanged = view !== S.view;
  const prevParam = S.param;
  S.view = view;
  S.param = param;

  if (view === 'applications') {
    if (viewChanged) renderView();
    if (param) { if (param !== S.drawerId) openDrawer(param); } else if (S.drawerId) closeDrawer(false);
  } else {
    if (S.drawerId) closeDrawer(false);
    if (viewChanged || (view === 'activity' && param !== prevParam)) renderView();
  }
}

function renderView(opts = {}) {
  const root = $('#view');
  const token = ++S.renderToken;
  const p = VIEWS[S.view](root, token, opts);
  if (p && p.catch) {
    p.catch((err) => {
      if (token !== S.renderToken) return;
      fill(root, h('div', { class: 'card empty' }, h('h2', null, 'Something went wrong'), h('p', null, err.message)));
    });
  }
}
const stale = (token) => token !== S.renderToken;

// ------------------------------------------------------------------ status pill & run button

async function pollStatus() {
  let delay = 10000;
  try {
    const st = await api('GET', '/api/status');
    S.status = st;
    renderPill(st);
    if (st.running) delay = 2000;
    if (S.jobsVersion !== null && st.jobs_version !== S.jobsVersion) onJobsChanged();
    S.jobsVersion = st.jobs_version;
  } catch {
    const pill = $('#botPill');
    pill.className = 'bot-pill error';
    $('.label', pill).textContent = 'Tracker server offline';
  }
  clearTimeout(S.pollTimer);
  S.pollTimer = setTimeout(pollStatus, delay);
}
function pollSoon() { clearTimeout(S.pollTimer); S.pollTimer = setTimeout(pollStatus, 700); }

function renderPill(st) {
  const pill = $('#botPill');
  const label = $('.label', pill);
  const btn = $('#runBtn');
  if (st.running) {
    const r = st.running;
    pill.className = 'bot-pill running';
    label.textContent = r.companies_total
      ? `Bot running · ${r.companies_done}/${r.companies_total} companies`
      : 'Bot starting…';
    pill.href = `#/activity/${r.id}`;
    btn.disabled = true;
    btn.textContent = 'Running…';
  } else {
    const last = st.last;
    pill.className = 'bot-pill ' + (!last ? '' : last.status === 'ok' ? 'ok' : 'error');
    label.textContent = last
      ? `Last run ${ago(last.finished_at || last.started_at)}${last.status === 'ok' ? '' : ` (${last.status})`}`
      : (S.me?.cloud ? `Runs ${S.me.run_schedule}` : 'Bot has not run yet');
    pill.href = last ? `#/activity/${last.id}` : '#/activity';
    btn.disabled = !S.me?.can_run;
    btn.textContent = 'Run bot now';
  }
}

function matchedText(m) {
  if (!m) return '';
  if (m.profile_incomplete) return 'Add skills in Settings so the bot can match internships for you.';
  const added = m.new || 0;
  if (!m.checked) return 'No new postings since your last match.';
  return `Matched you against ${m.checked} recent posting${m.checked === 1 ? '' : 's'}: ${added} new for you.`;
}

async function startRun() {
  const btn = $('#runBtn');
  btn.disabled = true;
  try {
    const r = await api('POST', '/api/runs');
    toast([matchedText(r.matched), r.note].filter(Boolean).join(' '));
    if (r.matched && r.matched.new) softRefreshJobs();
    setTimeout(pollStatus, 1200);
    if (S.view === 'activity' && r.started) setTimeout(() => renderView(), 1500);
    if (!r.started) btn.disabled = false;
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
  }
}

function onJobsChanged() {
  if (S.view === 'applications' && !Object.keys(S.pending).length) softRefreshJobs();
  else if (S.view === 'dashboard') renderView();
}

// ------------------------------------------------------------------ applications

const SORTS = {
  score: (a, b) => (a.score ?? -1) - (b.score ?? -1),
  found: (a, b) => (a.date_found || '').localeCompare(b.date_found || '') || a.id - b.id,
  company: (a, b) => a.company.localeCompare(b.company) || a.role.localeCompare(b.role),
  status: (a, b) => S.meta.statuses.indexOf(a.status) - S.meta.statuses.indexOf(b.status),
  applied: (a, b) => (a.applied_date || '').localeCompare(b.applied_date || ''),
  followup: (a, b) => (a.followup_date || '9999').localeCompare(b.followup_date || '9999'),
};
const isOverdue = (j) => j.status === 'Applied' && j.followup_date && j.followup_date <= todayIso();

function saveFilters() { store.set('filters', S.filters); }

function matchesFilters(j, ignoreStatus = false) {
  const f = S.filters;
  if (!ignoreStatus && f.statuses.length && !f.statuses.includes(j.status)) return false;
  if (f.hideClosed && !j.is_open) return false;
  if (f.hideSkipped && j.status === 'Skipped (red flag)') return false;
  if ((j.score ?? 0) < f.minScore) return false;
  if (f.flag === 'ai' && !j.ai_screening) return false;
  if (f.flag === 'red' && !(j.red_flags || []).length) return false;
  if (f.flag === 'night' && !j.night_shift) return false;
  if (f.flag === 'overdue' && !isOverdue(j)) return false;
  if (f.flag === 'unpaid' && j.paid !== 'No') return false;
  if (f.source === 'boards' && (FEEDS.has(j.source) || j.source === 'manual')) return false;
  if (f.source && f.source !== 'boards' && j.source !== f.source) return false;
  const q = f.q.trim().toLowerCase();
  if (q) {
    const hay = `${j.role} ${j.company} ${j.location || ''} ${j.region || ''} ${(j.missing_skills || []).join(' ')} ${(j.matched_skills || []).join(' ')} ${j.user_notes || ''} ${j.contact || ''}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function visibleJobs() {
  const cmp = SORTS[S.filters.sort] || SORTS.found;
  const dir = S.filters.dir === 'asc' ? 1 : -1;
  return S.jobs.filter((j) => matchesFilters(j)).sort((a, b) => dir * cmp(a, b) || b.id - a.id);
}

async function renderApplications(root, token) {
  if (!S.jobs.length) fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const [{ jobs }, status] = await Promise.all([api('GET', '/api/jobs'), api('GET', '/api/status').catch(() => null)]);
  if (stale(token)) return;
  S.jobs = jobs;
  const f = S.filters;

  const search = h('input', {
    class: 'input', type: 'search', placeholder: 'Search role, company, skill, notes…  ( / )', value: f.q,
    'aria-label': 'Search', id: 'search',
    oninput: (e) => { f.q = e.target.value; saveFilters(); refreshList(); },
  });
  const score = h('input', {
    type: 'range', min: 0, max: 100, step: 5, value: f.minScore, 'aria-label': 'Minimum match score',
    oninput: (e) => { f.minScore = Number(e.target.value); scoreOut.textContent = f.minScore; saveFilters(); refreshList(); },
  });
  const scoreOut = h('b', { class: 'num' }, f.minScore);
  const flagSel = h('select', { class: 'select', 'aria-label': 'Flag filter', onchange: (e) => { f.flag = e.target.value; saveFilters(); refreshList(); } },
    [['', 'Any flags'], ['overdue', 'Follow-up due'], ['ai', 'AI screening likely'], ['red', 'Has red flags'], ['night', 'Night-shift compatible'], ['unpaid', 'Unpaid']]
      .map(([v, l]) => h('option', { value: v, selected: f.flag === v }, l)));
  const sourceSel = h('select', { class: 'select', 'aria-label': 'Source filter', onchange: (e) => { f.source = e.target.value; saveFilters(); refreshList(); } },
    [['', 'All sources'], ['boards', 'Company boards'],
      ...Object.entries(SOURCE_LABELS).filter(([k]) => FEEDS.has(k)).map(([k, v]) => [k, v]), ['manual', 'Added by you']]
      .map(([v, l]) => h('option', { value: v, selected: (f.source || '') === v }, l)));
  const sortSel = h('select', { class: 'select', 'aria-label': 'Sort', onchange: (e) => { const [k, d] = e.target.value.split(':'); f.sort = k; f.dir = d; saveFilters(); refreshList(); } },
    [['found:desc', 'Newest found'], ['score:desc', 'Best match'], ['followup:asc', 'Follow-up soonest'], ['applied:desc', 'Recently applied'], ['company:asc', 'Company A-Z'], ['status:asc', 'Status']]
      .map(([v, l]) => h('option', { value: v, selected: `${f.sort}:${f.dir}` === v }, l)));

  const demoNote = S.meta.demo
    ? h('div', { class: 'banner' }, icon('info', 'i'), h('div', null, 'You are looking at ', h('b', null, 'sample data'),
      ' (fictional companies, example.com links). Run ', h('code', null, 'python serve.py'), ' without --demo for your real tracker.'))
    : null;

  fill(root,
    h('div', { class: 'view-head' },
      h('div', null, h('h1', null, 'Applications'),
        h('p', null, 'Internships the bot found and prepared. Review each one, apply on the company site yourself, then update its status here.')),
      h('div', { style: { display: 'flex', gap: '8px' } },
        h('a', { class: 'btn', href: '/api/jobs.csv', download: '' }, 'Export CSV'),
        h('button', { class: 'btn primary', type: 'button', onclick: () => openAddDialog() }, '+ Add internship'))),
    demoNote,
    S.me.profile_complete ? null : h('div', { class: 'banner warn-banner' }, icon('alert', 'i'),
      h('div', { style: { flex: 1 } }, h('b', null, 'Add your skills to get matches. '),
        'The bot only adds internships for accounts with skills in their profile. It takes two minutes.'),
      h('a', { class: 'btn sm primary', href: '#/settings' }, 'Set up my profile')),
    skippedBanner(status),
    h('div', { class: 'toolbar' },
      h('div', { class: 'search' }, icon('search'), search),
      flagSel, sourceSel, sortSel,
      h('label', { class: 'range' }, 'Min score', score, scoreOut),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.hideSkipped, onchange: (e) => { f.hideSkipped = e.target.checked; saveFilters(); refreshList(); } }), 'Hide red-flag skips'),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.hideClosed, onchange: (e) => { f.hideClosed = e.target.checked; saveFilters(); refreshList(); } }), 'Hide closed')),
    h('div', { class: 'chips', id: 'chips' }),
    h('div', { class: 'card', id: 'listCard' }),
  );
  refreshList();
}

async function softRefreshJobs() {
  try {
    const { jobs } = await api('GET', '/api/jobs');
    S.jobs = jobs;
    if (S.view === 'applications' && $('#listCard')) refreshList();
  } catch { /* next poll will retry */ }
}

function refreshList() {
  const chips = $('#chips');
  const card = $('#listCard');
  if (!chips || !card) return;
  const f = S.filters;

  const counts = {};
  for (const j of S.jobs) if (matchesFilters(j, true)) counts[j.status] = (counts[j.status] || 0) + 1;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const toggle = (st) => {
    f.statuses = st == null ? [] : f.statuses.includes(st) ? f.statuses.filter((x) => x !== st) : [...f.statuses, st];
    saveFilters();
    refreshList();
  };
  fill(chips,
    h('button', { class: 'chip', type: 'button', 'aria-pressed': String(!f.statuses.length), onclick: () => toggle(null) }, 'All', h('span', { class: 'count' }, total)),
    S.meta.statuses.map((st) => h('button', {
      class: 'chip', type: 'button', 'data-status': st, 'aria-pressed': String(f.statuses.includes(st)), onclick: () => toggle(st),
    }, h('span', { class: 'status-dot' }), st, h('span', { class: 'count' }, counts[st] || 0))),
  );

  const jobs = visibleJobs();
  if (!S.jobs.length) {
    fill(card, h('div', { class: 'empty' },
      h('h2', null, 'Nothing tracked yet'),
      h('p', null, 'The bot adds internships here when they are remote, open to India and match your skills. ',
        'Company boards mostly list on-site internships in the US, so the biggest supply is on platforms like Internshala: ',
        'browse them from the Platforms page and paste links into + Add internship.'),
      h('div', { style: { display: 'flex', gap: '8px', justifyContent: 'center', flexWrap: 'wrap' } },
        h('button', { class: 'btn primary', type: 'button', onclick: () => openAddDialog() }, '+ Add internship'),
        h('a', { class: 'btn', href: '#/platforms' }, 'Browse platforms'),
        S.me.can_run ? h('button', { class: 'btn', type: 'button', onclick: startRun }, 'Run bot now') : null)));
    return;
  }
  if (!jobs.length) {
    fill(card, h('div', { class: 'empty' }, h('h2', null, 'No matches for these filters'),
      h('button', { class: 'btn', type: 'button', onclick: () => {
        Object.assign(f, { q: '', statuses: [], minScore: 0, flag: '', source: '', hideClosed: false, hideSkipped: false });
        saveFilters(); renderView();
      } }, 'Clear filters')));
    return;
  }

  const head = (key, label, cls) => {
    const active = f.sort === key;
    return h('th', { class: cls, 'aria-sort': active ? (f.dir === 'asc' ? 'ascending' : 'descending') : null },
      key ? h('button', { type: 'button', onclick: () => {
        f.dir = active && f.dir === 'desc' ? 'asc' : 'desc';
        f.sort = key; saveFilters(); refreshList();
      } }, label, active ? (f.dir === 'asc' ? ' ↑' : ' ↓') : '') : label);
  };
  fill(card,
    h('div', { class: 'table-wrap' },
      h('table', { class: 'tracker' },
        h('thead', null, h('tr', null,
          head('score', 'Score'), head('company', 'Role'), head(null, 'Remote / region'), head(null, 'Hours'),
          head(null, 'Paid'), head(null, 'Flags'), head('status', 'Status'), head('applied', 'Applied'),
          head('followup', 'Follow-up'), head('found', 'Found'))),
        h('tbody', null, jobs.map(jobRow)))),
    h('div', { class: 'list-footer' },
      h('span', null, `Showing ${jobs.length} of ${plural(S.jobs.length, 'internship')}`),
      h('span', null, 'The bot never submits. You apply on the company’s site.')),
  );
}

function scoreCell(score) {
  const v = score ?? 0;
  const cls = v > 75 ? 'hi' : v >= 50 ? 'mid' : 'lo';
  const bar = h('i');
  bar.style.width = `${v}%`;
  return h('div', { class: `score ${cls}`, title: `Match score ${v}/100` }, h('b', null, score ?? '–'), h('div', { class: 'bar' }, bar));
}

function flagBadges(j) {
  const out = [];
  if ((j.red_flags || []).length) out.push(h('span', { class: 'badge red', title: j.red_flags.join('\n') }, icon('flag'), plural(j.red_flags.length, 'red flag')));
  if (j.ai_screening) out.push(h('span', { class: 'badge ai', title: (j.ai_reasons || []).join('\n') || 'AI screening likely' }, icon('ai'), 'AI screening'));
  if (j.night_shift) out.push(h('span', { class: 'badge night', title: 'Mentions US hours, which works with your night availability' }, icon('moon'), 'Night shift OK'));
  if (j.paid === 'No') out.push(h('span', { class: 'badge warn', title: 'Unpaid: review manually' }, 'Unpaid'));
  if (j.excluded_reason) out.push(h('span', { class: 'badge warn', title: j.excluded_reason }, icon('alert'), 'Now filtered out'));
  if (!j.is_open) out.push(h('span', { class: 'badge closed', title: 'No longer listed on the company board' }, icon('lock'), 'Closed'));
  return out;
}

function statusSelect(value, onChange, label = 'Status') {
  const sel = h('select', { class: 'status-select', 'data-status': value, 'aria-label': label },
    S.meta.statuses.map((st) => h('option', { value: st, selected: st === value }, st)));
  sel.addEventListener('change', () => { sel.dataset.status = sel.value; onChange(sel.value); });
  return sel;
}

function jobRow(j) {
  const open = () => go(`#/applications/${j.id}`);
  const overdue = isOverdue(j);
  const tr = h('tr', {
    'data-status': j.status, 'data-id': j.id, tabindex: 0,
    class: [!j.is_open && 'closed', S.drawerId === j.id && 'selected'].filter(Boolean).join(' ') || null,
    onclick: (e) => { if (!e.target.closest('select, a, button, input')) open(); },
    onkeydown: (e) => { if (e.key === 'Enter' && e.target === tr) open(); },
  },
  h('td', { class: 'c-score' }, scoreCell(j.score)),
  h('td', { class: 'c-role' },
    h('div', { class: 'role-name' }, j.role),
    h('div', { class: 'role-sub' }, j.company, h('span', { class: 'src' }, ` · ${jobOrigin(j)}`))),
  h('td', { class: 'c-where' }, h('div', { class: 'where' }, h('b', null, j.remote_type || '–'), h('br'), j.region || '')),
  h('td', { class: 'c-hours' }, h('span', { class: 'where' }, j.hours_label || '–')),
  h('td', { class: 'c-paid' }, h('span', { class: 'where' }, j.paid || '–')),
  h('td', { class: 'c-flags' }, h('div', { class: 'badges' }, flagBadges(j))),
  h('td', { class: 'c-status' }, statusSelect(j.status, (v) => changeStatus(j.id, v))),
  h('td', { class: 'c-applied' }, h('span', { class: 'date' }, fmtDate(j.applied_date))),
  h('td', { class: 'c-followup' + (overdue ? ' overdue' : ''), title: overdue ? 'Follow-up is due' : null },
    j.followup_date ? h('span', { class: 'date' }, fmtDate(j.followup_date)) : null),
  h('td', { class: 'c-found' }, h('span', { class: 'date' }, fmtDate(j.date_found))));
  return tr;
}

async function changeStatus(id, status) {
  try {
    const job = await saveJob(id, { status });
    let msg = `Status set to ${status}`;
    if (status === 'Applied' && job.followup_date) msg += ` · follow up ${fmtDate(job.followup_date)}`;
    toast(msg);
    refreshList();
    if (S.drawerId === id) renderDrawer(job);
  } catch (err) {
    toast(err.message, true);
    softRefreshJobs();
  }
}

async function saveJob(id, changes) {
  const job = await api('PATCH', `/api/jobs/${id}`, changes);
  const i = S.jobs.findIndex((j) => j.id === id);
  if (i >= 0) {
    const { description, note, answers, history, ...listFields } = job;
    S.jobs[i] = { ...S.jobs[i], ...listFields };
  }
  S.jobsVersion = null; // our own edit; don't treat the next poll as an outside change
  return job;
}

// ------------------------------------------------------------------ drawer

async function openDrawer(id) {
  await flushPending();
  S.drawerId = id;
  const drawer = $('#drawer');
  $('#scrim').hidden = false;
  drawer.hidden = false;
  fill(drawer, h('div', { class: 'drawer-body muted' }, 'Loading…'));
  document.querySelectorAll('table.tracker tbody tr').forEach((tr) => tr.classList.toggle('selected', Number(tr.dataset.id) === id));
  try {
    const job = await api('GET', `/api/jobs/${id}`);
    if (S.drawerId === id) renderDrawer(job);
  } catch (err) {
    fill(drawer, h('div', { class: 'drawer-body' }, h('p', null, err.message)));
  }
}

async function closeDrawer(updateHash = true) {
  await flushPending();
  S.drawerId = null;
  $('#drawer').hidden = true;
  $('#scrim').hidden = true;
  document.querySelectorAll('table.tracker tbody tr.selected').forEach((tr) => tr.classList.remove('selected'));
  if (updateHash && S.view === 'applications') history.replaceState(null, '', '#/applications');
  S.param = null;
}

function queueSave(id, field, value, note) {
  S.pending[field] = value;
  note.textContent = 'Saving…';
  note.className = 'saved-note';
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(() => flushPending(note), 650);
  S.pendingId = id;
  S.pendingNote = note;
}

async function flushPending(note = S.pendingNote) {
  clearTimeout(S.saveTimer);
  const changes = S.pending;
  if (!Object.keys(changes).length) return;
  S.pending = {};
  try {
    await saveJob(S.pendingId, changes);
    if (note && note.isConnected) { note.textContent = 'Saved'; note.className = 'saved-note ok'; }
    if (S.view === 'applications') refreshList();
  } catch (err) {
    if (note && note.isConnected) { note.textContent = `Not saved: ${err.message}`; note.className = 'saved-note err'; }
    else toast(`Not saved: ${err.message}`, true);
  }
}

function copyButton(getText) {
  return h('button', { class: 'btn sm', type: 'button', onclick: async () => {
    try { await navigator.clipboard.writeText(getText()); toast('Copied to clipboard'); }
    catch { toast('Copy failed: select the text and press Ctrl+C', true); }
  } }, 'Copy');
}

function flagItems(j) {
  const items = [];
  for (const r of j.red_flags || []) items.push(['red', 'flag', r]);
  for (const r of j.ai_reasons || []) items.push(['ai', 'ai', `AI screening likely: ${r}`]);
  if (j.night_shift) items.push(['info', 'moon', 'Night-shift compatible (mentions US hours)']);
  for (const r of j.warnings || []) items.push(['warn', 'alert', r]);
  if (j.excluded_reason) items.push(['warn', 'alert', `No longer passes your filters: ${j.excluded_reason}`]);
  if (!j.is_open) items.push(['info', 'lock', 'No longer listed on the company board (closed)']);
  return items;
}

function renderDrawer(j) {
  const drawer = $('#drawer');
  const savedNote = h('div', { class: 'saved-note', 'aria-live': 'polite' });
  const save = (field) => (e) => queueSave(j.id, field, e.target.value, savedNote);
  const canApply = ['To Review', 'Ready to Apply'].includes(j.status);

  const scoreFill = h('i');
  scoreFill.style.width = `${j.score ?? 0}%`;
  scoreFill.style.background = (j.score ?? 0) > 75 ? 'var(--good)' : (j.score ?? 0) >= 50 ? 'var(--warn)' : 'var(--critical)';
  const detail = j.score_detail || {};

  const noteArea = h('textarea', { class: 'textarea', rows: 9, value: j.note || '', 'aria-label': 'Tailored note', oninput: save('note') });
  const answersArea = h('textarea', { class: 'textarea mono', rows: 14, value: j.answers || '', 'aria-label': 'Prefilled answers', oninput: save('answers') });
  const flags = flagItems(j);

  fill(drawer,
    h('div', { class: 'drawer-head' },
      h('div', { class: 'row' },
        h('div', null,
          h('h1', null, j.role),
          h('div', { class: 'sub' }, `${j.company} · ${jobOrigin(j)} · found ${fmtDate(j.date_found, true)}`)),
        h('button', { class: 'btn ghost sm', type: 'button', 'aria-label': 'Close', onclick: () => closeDrawer() }, icon('x', 'i'), 'Close')),
      h('div', { class: 'drawer-actions' },
        h('a', { class: 'btn primary', href: j.apply_url || j.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open application form ↗'),
        h('a', { class: 'btn', href: j.url, target: '_blank', rel: 'noopener noreferrer' }, FEEDS.has(j.source) ? `View on ${sourceLabel(j.source)} ↗` : 'View posting ↗'),
        canApply ? h('button', { class: 'btn', type: 'button', onclick: () => changeStatus(j.id, 'Applied') }, icon('check', 'i'), 'I applied') : null,
        h('button', { class: 'btn ghost danger', type: 'button', title: 'Pay-to-train outfit, fake company, or scam: block it',
          onclick: async () => {
            if (!confirm(`Mark ${j.company} as not a real employer?\n\nIts listings move to Skipped (red flag), and future ones are flagged automatically. You can undo this in Settings → Advanced → Blocked companies.`)) return;
            try {
              const r = await api('POST', `/api/jobs/${j.id}/block`);
              toast(`Blocked ${r.company}${r.moved ? ` · ${plural(r.moved, 'listing')} moved to Skipped` : ''}`);
              await softRefreshJobs();
              renderDrawer(r.job);
            } catch (err) { toast(err.message, true); }
          } }, icon('flag', 'i'), 'Not a real employer'))),
    h('div', { class: 'drawer-body' },
      h('div', { class: 'notice' }, 'The bot never submits anything. Copy the note and answers below, apply on the company’s site, then set the status to Applied.'),

      h('section', { class: 'section' },
        h('div', { class: 'section-head' }, h('h3', null, 'Tracking'), savedNote),
        h('div', { class: 'form-grid' },
          h('label', { class: 'field' }, h('span', null, 'Status'), statusSelect(j.status, (v) => changeStatus(j.id, v))),
          h('label', { class: 'field' }, h('span', null, 'Contact / referral'), h('input', { class: 'input', value: j.contact || '', placeholder: 'Recruiter, referrer…', oninput: save('contact') })),
          h('label', { class: 'field' }, h('span', null, 'Applied date'), h('input', { class: 'input', type: 'date', value: j.applied_date || '', onchange: save('applied_date') })),
          h('label', { class: 'field' }, h('span', null, 'Follow-up date'), h('input', { class: 'input', type: 'date', value: j.followup_date || '', onchange: save('followup_date') })),
          h('label', { class: 'field full' }, h('span', null, 'Notes'), h('textarea', { class: 'textarea', rows: 3, value: j.user_notes || '', placeholder: 'Anything you want to remember…', oninput: save('user_notes') })))),

      h('section', { class: 'section' },
        h('h3', null, 'Match'),
        h('div', { class: 'score-big' }, h('b', { class: 'num' }, j.score ?? '–'), h('div', { class: 'meter' }, scoreFill)),
        h('div', { class: 'small muted' },
          `${detail.formula || ''}${detail.skill_fit != null ? ` · skill fit ${Math.round(detail.skill_fit * 100)}%` : ''}${detail.title_relevance != null ? ` · title relevance ${Math.round(detail.title_relevance * 100)}%` : ''}`),
        h('div', { class: 'skill-chips' },
          (j.matched_skills || []).map((x) => h('span', { class: 'skill have', title: 'You have this' }, `✓ ${x}`)),
          (j.missing_skills || []).map((x) => h('span', { class: 'skill miss', title: 'Mentioned in the posting; not in your profile' }, x)),
          !(j.matched_skills || []).length && !(j.missing_skills || []).length ? h('span', { class: 'small muted' }, 'No known skills mentioned in the posting.') : null)),

      flags.length ? h('section', { class: 'section' },
        h('h3', null, 'Flags'),
        h('ul', { class: 'flag-list' }, flags.map(([cls, ic, text]) => h('li', { class: cls }, icon(ic), h('span', null, text))))) : null,

      h('section', { class: 'section' },
        h('h3', null, 'Details'),
        h('dl', { class: 'kv' },
          ...[['Remote', j.remote_type], ['Region allowed', j.region], ['Location', j.location], ['Hours / week', j.hours_label],
            ['Paid?', j.paid], ['Compensation', j.compensation], ['Posted', fmtDate(j.posted_at, true)], ['Last seen', j.last_seen ? ago(j.last_seen) : '']]
            .filter(([, v]) => v).flatMap(([k, v]) => [h('dt', null, k), h('dd', null, v)]))),

      h('section', { class: 'section' },
        h('div', { class: 'section-head' }, h('h3', null, 'Tailored note'), copyButton(() => noteArea.value)),
        noteArea),
      h('section', { class: 'section' },
        h('div', { class: 'section-head' }, h('h3', null, 'Prefilled answers'), copyButton(() => answersArea.value)),
        answersArea,
        h('div', null, h('button', { class: 'btn sm ghost', type: 'button', onclick: async () => {
          if (j.drafts_edited && !confirm('Replace your edited note and answers with freshly generated ones?')) return;
          try {
            const updated = await api('POST', `/api/jobs/${j.id}/regenerate`);
            renderDrawer(updated);
            toast('Drafts regenerated from your current profile');
          } catch (err) { toast(err.message, true); }
        } }, 'Regenerate from profile'),
        h('span', { class: 'small muted' }, j.drafts_edited ? '  You have edited these drafts; the bot won’t overwrite them.' : '  Edits are saved automatically.'))),

      j.description ? h('section', { class: 'section' },
        h('details', null, h('summary', null, 'Full posting text'), h('div', { class: 'desc' }, j.description))) : null,

      (j.history || []).length ? h('section', { class: 'section' },
        h('h3', null, 'History'),
        h('ul', { class: 'timeline' }, j.history.map((ev) => h('li', null,
          h('span', { class: 'status-pill', 'data-status': ev.to_status }, ev.to_status),
          h('span', null, ev.from_status ? `from ${ev.from_status}` : 'found by the bot'),
          h('span', { class: 'muted' }, `· ${fmtDate(ev.at, true)}`))))) : null,
    ),
  );
}

function skippedBanner(status) {
  const last = status && status.last;
  const c = last && last.counts;
  if (!c || !c.skipped) return null;
  const reasons = Object.entries(c.skip_reasons || {}).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${n} ${(S.meta.reason_labels[k] || k).toLowerCase()}`).join(', ');
  const all = c.skipped >= (c.internships || 0);
  return h('div', { class: 'banner warn-banner' }, icon('alert', 'i'),
    h('div', { style: { flex: 1 } },
      h('b', null, `The last run found ${plural(c.internships || 0, 'internship')} and skipped ${all ? 'all of them' : c.skipped}`),
      ` (${reasons}). `,
      'They are listed with the exact reason; if one is wrong for you, track it anyway. ',
      'If you would take on-site or hybrid work in a city like Pune, add it under Settings → Filters → On-site OK locations.'),
    h('button', { class: 'btn sm', type: 'button', onclick: () => { store.set('activityTab', 'skipped'); go(`#/activity/${last.id}`); } }, 'See skipped internships'));
}

// ------------------------------------------------------------------ add internship

const PLATFORM_CHOICES = ['Internshala', 'Unstop', 'Wellfound', 'YC Work at a Startup', 'Hacker News', 'Remote.co', 'Arc.dev',
  'Handshake', 'WayUp', 'Otta / Welcome to the Jungle', 'LinkedIn', 'Company website', 'Referral', 'Google Summer of Code',
  'LFX Mentorship', 'MLH Fellowship', 'Outreachy', 'Other'];

function openAddDialog(prefill = {}) {
  S.drawerId = 'add';
  const drawer = $('#drawer');
  $('#scrim').hidden = false;
  drawer.hidden = false;

  const v = { status: 'To Review', workplace: 'remote', ...prefill };
  const inputs = {};
  const field = (key, label, el, cls) => {
    inputs[key] = el;
    if (v[key] != null && el.tagName !== 'SELECT') el.value = v[key];
    return h('label', { class: 'field' + (cls ? ` ${cls}` : '') }, h('span', null, label), el);
  };
  const msg = h('div', { class: 'saved-note', 'aria-live': 'polite' });
  const urlInput = h('input', { class: 'input', type: 'url', placeholder: 'https://internshala.com/internship/detail/…', value: v.url || '',
    onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); readLink(); } } });
  const readBtn = h('button', { class: 'btn', type: 'button', onclick: () => readLink() }, 'Read details');
  const sel = (key, options) => h('select', { class: 'select' }, options.map(([val, lab]) => h('option', { value: val, selected: (v[key] || '') === val }, lab)));
  const appliedWrap = h('div');
  const statusSel = sel('status', ['To Review', 'Ready to Apply', 'Applied'].map((x) => [x, x]));
  const appliedInput = h('input', { class: 'input', type: 'date', value: todayIso() });
  const syncApplied = () => fill(appliedWrap, statusSel.value === 'Applied'
    ? h('label', { class: 'field' }, h('span', null, 'Applied on'), appliedInput) : null);
  statusSel.addEventListener('change', syncApplied);
  const datalist = h('datalist', { id: 'platformList' }, PLATFORM_CHOICES.map((p) => h('option', { value: p })));

  const setFields = (f) => {
    for (const [k, el] of Object.entries(inputs)) {
      if (f[k] == null || f[k] === '') continue;
      el.value = f[k];
    }
  };

  async function readLink() {
    const url = urlInput.value.trim();
    if (!url) { urlInput.focus(); return; }
    readBtn.disabled = true;
    msg.className = 'saved-note';
    msg.textContent = 'Reading the page…';
    try {
      const r = await api('POST', '/api/jobs/lookup', { url });
      setFields({ ...r.fields, url });
      msg.textContent = r.message;
      msg.className = 'saved-note ' + (r.ok ? 'ok' : 'err');
    } catch (err) {
      msg.textContent = err.message;
      msg.className = 'saved-note err';
    }
    readBtn.disabled = false;
  }

  const saveBtn = h('button', { class: 'btn primary', type: 'button' }, 'Add to tracker');
  const errorBox = h('div', { class: 'error-box', hidden: true });
  saveBtn.onclick = async () => {
    const body = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()]));
    body.url = urlInput.value.trim();
    body.status = statusSel.value;
    if (body.status === 'Applied') body.applied_date = appliedInput.value;
    if (!body.role || !body.company) {
      errorBox.textContent = 'Role and company are required.';
      errorBox.hidden = false;
      return;
    }
    saveBtn.disabled = true;
    errorBox.hidden = true;
    try {
      const r = await api('POST', '/api/jobs/manual', body);
      toast(r.created ? (r.note ? 'Added (your filters would have skipped it; see Flags)' : 'Added to your tracker') : 'Already in your tracker');
      S.drawerId = null;
      await softRefreshJobs();
      if (S.view !== 'applications') S.view = null;
      go(`#/applications/${r.job_id}`);
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      saveBtn.disabled = false;
    }
  };

  fill(drawer,
    h('div', { class: 'drawer-head' },
      h('div', { class: 'row' },
        h('div', null, h('h1', null, 'Add an internship'),
          h('div', { class: 'sub' }, 'For roles you find on Internshala, Unstop, Wellfound, HN, a company site, or through a referral.')),
        h('button', { class: 'btn ghost sm', type: 'button', 'aria-label': 'Close', onclick: () => closeDrawer() }, icon('x', 'i'), 'Close'))),
    h('div', { class: 'drawer-body' },
      h('section', { class: 'section' },
        h('h3', null, '1. Paste the link'),
        h('div', { style: { display: 'flex', gap: '8px' } }, urlInput, readBtn),
        msg,
        h('div', { class: 'small muted' }, 'The tracker reads only that one page, and only if the site allows automated requests. ',
          'Sites that need a login or block bots (Wellfound, Handshake, LinkedIn) can’t be read; fill the form instead. Nothing is ever submitted.')),
      h('section', { class: 'section' },
        h('h3', null, '2. Check the details'),
        h('div', { class: 'form-grid' },
          field('role', 'Role *', h('input', { class: 'input', placeholder: 'Front-end Development Intern' }), 'full'),
          field('company', 'Company *', h('input', { class: 'input' })),
          field('platform', 'Found on', h('input', { class: 'input', list: 'platformList', placeholder: 'Internshala' })),
          field('workplace', 'Work type', sel('workplace', [['remote', 'Remote'], ['hybrid', 'Hybrid'], ['onsite', 'On-site'], ['', 'Not sure']])),
          field('location', 'Location / who can apply', h('input', { class: 'input', placeholder: 'Remote (India)' })),
          field('employment_type', 'Type', h('input', { class: 'input', placeholder: 'Internship, part-time' })),
          field('compensation', 'Stipend / pay', h('input', { class: 'input', placeholder: '₹10,000 / month' })),
          field('apply_url', 'Application link (if different)', h('input', { class: 'input', type: 'url' })),
          field('company_url', 'Company website', h('input', { class: 'input', type: 'url', placeholder: 'https://…' })),
          field('description', 'Description (paste it; used for scoring, flags and the drafted note)', h('textarea', { class: 'textarea', rows: 8 }), 'full'),
          h('label', { class: 'field' }, h('span', null, 'Status'), statusSel),
          appliedWrap),
        datalist),
      errorBox,
      h('div', { style: { display: 'flex', gap: '8px', justifyContent: 'flex-end' } },
        h('button', { class: 'btn ghost', type: 'button', onclick: () => closeDrawer() }, 'Cancel'), saveBtn)),
  );
  syncApplied();
  if (v.url && !prefill.noAutoRead) readLink(); else urlInput.focus();
}

// ------------------------------------------------------------------ platforms

const MODE_INFO = {
  auto: ['Automatic', 'The bot checks this every run.'],
  watch: ['Dates watched', 'The bot checks this program\'s timeline every day.'],
  link: ['Paste link', 'No public feed. Paste an internship link into + Add internship and the details are read for you.'],
  manual: ['Not automatable', 'Needs a login or blocks automated requests, so the bot can\'t use it.'],
  form: ['Fill form', 'Needs a login or blocks automated requests: add roles with the + Add internship form.'],
  program: ['Program', 'Seasonal program; apply on its site and track it with + Add internship.'],
};

async function renderPlatforms(root, token) {
  fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const data = await api('GET', '/api/platforms');
  if (stale(token)) return;
  const card = (p) => {
    const [modeLabel, modeHint] = MODE_INFO[p.mode] || [p.mode, ''];
    const st = p.state;
    const ps = p.program_status;
    const external = /^https?:/.test(p.url || '');
    const off = p.mode === 'auto' && p.source && p.source !== 'boards' && p.enabled === false;
    return h('div', { class: 'card platform' + (off ? ' off' : '') },
      h('div', { class: 'platform-head' },
        h('h2', null, p.name),
        h('span', { class: `mode mode-${off ? 'manual' : p.mode}`, title: modeHint }, off ? 'Off' : modeLabel)),
      h('p', null, p.note || ''),
      ps ? h('div', { class: 'program-status' },
        h('b', null, ps.status || 'Not checked yet'),
        ps.detail ? h('div', { class: 'small' }, ps.detail) : null,
        ps.next_date ? h('div', { class: 'small' }, `Next: ${ps.next_label} on ${fmtDate(ps.next_date, true)}`) : null,
        h('div', { class: 'small muted' }, ps.error ? `Last check failed: ${ps.error}` : `Checked ${ago(ps.checked_at)}`)) : null,
      p.mode === 'watch' && !ps ? h('div', { class: 'small muted' }, 'Checked on the next bot run.') : null,
      st ? h('div', { class: 'small ' + (st.ok ? 'muted' : 'err-text') },
        st.ok ? `Last fetch ${ago(st.last_fetch_at)}: ${st.postings} postings, ${st.internships} matching, ${st.kept} kept` : st.message) : null,
      off ? h('div', { class: 'small muted' }, 'Switched off in Settings → Job feeds.') : null,
      h('div', { class: 'platform-foot' },
        h('span', { class: 'small muted' }, `${plural(p.tracked || 0, 'job')} tracked`),
        h('div', { style: { display: 'flex', gap: '6px' } },
          p.url ? h('a', { class: 'btn sm', href: p.url, target: external ? '_blank' : null, rel: external ? 'noopener noreferrer' : null }, external ? 'Open ↗' : 'Open') : null,
          !['auto', 'watch'].includes(p.mode) ? h('button', { class: 'btn sm primary', type: 'button',
            onclick: () => openAddDialog({ platform: p.name.split(' (')[0], noAutoRead: true }) }, '+ Add from here') : null)));
  };
  fill(root,
    h('div', { class: 'view-head' },
      h('div', null, h('h1', null, 'Platforms'),
        h('p', null, 'Everywhere internships come from. Automatic sources are fetched every run and program dates are checked daily. The few that need a login or block bots are listed at the bottom with the reason; for those, + Add internship gives roles the same scoring, flags, drafts and follow-ups.')),
      h('button', { class: 'btn primary', type: 'button', onclick: () => openAddDialog() }, '+ Add internship')),
    h('div', { class: 'banner' }, icon('info', 'i'), h('div', null,
      'Every automatic source is read politely: robots.txt respected, one request per second per site, no logins, and nothing that gets around bot checks. ',
      'Switch sources on or off and tune their keywords in Settings → Job feeds. This list lives in ', h('code', null, 'platforms.json'), '.')),
    (data.groups || []).map((g) => h('section', { class: 'platform-group' },
      h('h3', null, g.name),
      h('div', { class: 'platform-grid' }, (g.platforms || []).map(card)))));
}

// ------------------------------------------------------------------ dashboard

async function renderDashboard(root, token) {
  fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const st = await api('GET', '/api/stats');
  if (stale(token)) return;
  const goal = S.meta.goals || {};
  const gmin = goal.weekly_min ?? 10, gmax = goal.weekly_max ?? 15;

  const kpi = (label, value, foot, opts = {}) => h('div', {
    class: `card kpi${opts.alert ? ' alert' : ''}`,
    role: opts.onclick ? 'link' : null, tabindex: opts.onclick ? 0 : null, onclick: opts.onclick,
    style: opts.onclick ? { cursor: 'pointer' } : null,
  }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), h('div', { class: 'foot' }, foot || ''));
  const filterTo = (patch) => () => {
    Object.assign(S.filters, { q: '', statuses: [], minScore: 0, flag: '', source: '', hideClosed: false, hideSkipped: false }, patch);
    saveFilters();
    go('#/applications');
  };

  const scaleMax = Math.max(gmax + 3, st.this_week + 1);
  const meterFill = h('div', { class: 'fill' });
  meterFill.style.width = `${Math.min(100, (st.this_week / scaleMax) * 100)}%`;
  const band = h('div', { class: 'band', title: `Goal: ${gmin}-${gmax} per week` });
  band.style.left = `${(gmin / scaleMax) * 100}%`;
  band.style.width = `${((gmax - gmin) / scaleMax) * 100}%`;
  const remaining = Math.max(0, gmin - st.this_week);
  const weekMsg = st.this_week >= gmax ? 'Top of your goal range. Nice.'
    : st.this_week >= gmin ? `In your goal range. Up to ${gmax - st.this_week} more is still on target.`
      : `${plural(remaining, 'more application')} to reach your minimum of ${gmin} this week.`;

  const weeklyHost = h('div');
  const totalBy = Object.values(st.by_status).reduce((a, b) => a + b, 0);
  const maxStatus = Math.max(1, ...Object.values(st.by_status));
  const maxMissing = Math.max(1, ...st.top_missing.map(([, n]) => n));

  fill(root,
    h('div', { class: 'view-head' }, h('div', null, h('h1', null, 'Dashboard'),
      h('p', null, `Progress toward ${gmin}-${gmax} applications a week.`))),
    S.meta.demo ? h('div', { class: 'banner' }, icon('info', 'i'), 'Sample data.') : null,
    h('div', { class: 'kpis' },
      kpi('Total found', st.total, `${st.to_review} to review · ${st.ready} ready`, { onclick: filterTo({}) }),
      kpi('Applied', st.applied, 'all time'),
      kpi('Interviews', st.interviews, 'AI / scheduled / offer'),
      kpi('Offers', st.offers, '', { onclick: filterTo({ statuses: ['Offer'] }) }),
      kpi('Rejections', st.rejections, '', { onclick: filterTo({ statuses: ['Rejected'] }) }),
      kpi('Follow-ups due', st.followups_due, st.followups_due ? 'Click to see them' : 'All caught up',
        { alert: st.followups_due > 0, onclick: filterTo({ flag: 'overdue' }) })),
    h('div', { class: 'card card-pad', style: { marginTop: '16px' } },
      h('div', { class: 'week-hero' },
        h('div', null, h('div', { class: 'small muted' }, 'Applied this week'), h('div', { class: 'hero' }, st.this_week)),
        h('div', { class: 'meter-wrap' },
          h('div', { class: 'goal-meter', role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': scaleMax, 'aria-valuenow': st.this_week,
            'aria-label': `Applications this week: ${st.this_week}, goal ${gmin} to ${gmax}` }, band, meterFill),
          h('div', { class: 'goal-scale' }, h('span', null, '0'), h('span', null, `Goal ${gmin}-${gmax}`), h('span', null, scaleMax)),
          h('div', { class: 'small', style: { marginTop: '6px', color: 'var(--ink-2)' } }, weekMsg)))),
    h('div', { class: 'dash-grid' },
      h('div', { class: 'card card-pad' },
        h('div', { class: 'card-head' }, h('div', null, h('h2', null, 'Applications per week'),
          h('p', null, `Last 12 weeks, by applied date. Shaded band is your goal (${gmin}-${gmax}).`))),
        weeklyHost),
      h('div', { class: 'card card-pad' },
        h('div', { class: 'card-head' }, h('div', null, h('h2', null, 'By status'), h('p', null, `${totalBy} tracked. Click a row to filter.`))),
        h('div', { class: 'hbars' }, S.meta.statuses.map((name) => {
          const n = st.by_status[name] || 0;
          const bar = h('div', { class: 'bar' + (n ? '' : ' zero') });
          bar.style.width = `${(n / maxStatus) * 100}%`;
          return h('div', { class: 'hbar', role: 'link', tabindex: 0, 'data-status': name, onclick: filterTo({ statuses: [name] }),
            onkeydown: (e) => { if (e.key === 'Enter') filterTo({ statuses: [name] })(); } },
          h('span', { class: 'name' }, h('span', { class: 'status-dot' }), name),
          h('span', { class: 'track' }, bar, h('span', { class: 'v' }, n)));
        })))),
    h('div', { class: 'card card-pad', style: { marginTop: '16px' } },
      h('div', { class: 'card-head' }, h('div', null, h('h2', null, 'Skills you’re most often missing'),
        h('p', null, 'Mentioned in tracked postings but not in your profile. Good candidates to learn or to add if you already know them.'))),
      st.top_missing.length
        ? h('div', { class: 'hbars' }, st.top_missing.map(([name, n]) => {
          const bar = h('div', { class: 'bar' });
          bar.style.width = `${(n / maxMissing) * 100}%`;
          return h('div', { class: 'hbar' }, h('span', { class: 'name' }, name), h('span', { class: 'track' }, bar, h('span', { class: 'v' }, n)));
        }))
        : h('p', { class: 'muted' }, 'Nothing yet.')),
  );
  mountChart(weeklyHost, (w) => weeklyChart(st.weekly, gmin, gmax, w));
}

function mountChart(host, draw) {
  let last = 0;
  let ro;
  const render = () => {
    if (ro && !host.isConnected) { ro.disconnect(); return; }
    const w = Math.round(host.clientWidth);
    if (!w || w === last) return;
    last = w;
    fill(host, draw(w));
  };
  render();
  ro = new ResizeObserver(render);
  ro.observe(host);
}

function roundedTop(x, y, w, hgt, r) {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

function weeklyChart(weeks, gmin, gmax, width) {
  const H = 240, m = { t: 18, r: 8, b: 26, l: 28 };
  const iw = width - m.l - m.r, ih = H - m.t - m.b;
  const peak = Math.max(gmax, ...weeks.map((w) => w.count));
  const step = peak <= 10 ? 2 : peak <= 25 ? 5 : 10;
  const top = Math.ceil((peak + 1) / step) * step;
  const y = (v) => m.t + ih - (v / top) * ih;
  const band = iw / weeks.length;
  const bw = Math.min(24, band * 0.62);
  const labelEvery = width < 520 ? 3 : width < 760 ? 2 : 1;

  const root = s('svg', { class: 'chart', width, height: H, viewBox: `0 0 ${width} ${H}`, role: 'img',
    'aria-label': `Applications per week, last ${weeks.length} weeks: ${weeks.map((w) => w.count).join(', ')}` });
  for (let v = 0; v <= top; v += step) {
    root.append(s('line', { class: v === 0 ? 'baseline' : 'grid-line', x1: m.l, x2: width - m.r, y1: y(v), y2: y(v) }));
    root.append(s('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end' }, v));
  }
  root.append(s('rect', { class: 'goal-band', x: m.l, width: iw, y: y(gmax), height: y(gmin) - y(gmax) }));
  root.append(s('line', { class: 'goal-edge', x1: m.l, x2: width - m.r, y1: y(gmin), y2: y(gmin) }));
  root.append(s('text', { class: 'goal-label', x: width - m.r - 2, y: y(gmax) - 5, 'text-anchor': 'end' }, `Goal ${gmin}-${gmax}`));

  weeks.forEach((wk, i) => {
    const cx = m.l + band * i + band / 2;
    const isThis = i === weeks.length - 1;
    const g = s('g');
    if (wk.count > 0) {
      g.append(s('path', { class: 'col', d: roundedTop(cx - bw / 2, y(wk.count), bw, y(0) - y(wk.count), 4) }));
    }
    if (isThis || wk.count === Math.max(...weeks.map((w) => w.count))) {
      if (wk.count > 0) g.append(s('text', { class: 'value-label', x: cx, y: y(wk.count) - 6, 'text-anchor': 'middle' }, wk.count));
    }
    if (i % labelEvery === (weeks.length - 1) % labelEvery) {
      g.append(s('text', { x: cx, y: H - 8, 'text-anchor': 'middle' }, isThis ? 'This wk' : fmtDate(wk.week)));
    }
    const hit = s('rect', { class: 'hit', x: m.l + band * i, y: m.t, width: band, height: ih });
    const status = wk.count >= gmin ? (wk.count > gmax ? 'above goal' : 'in goal range') : 'below goal';
    hit.addEventListener('mousemove', (e) => showTip(e.clientX, e.clientY,
      h('div', null, h('b', null, isThis ? 'This week' : `Week of ${fmtDate(wk.week)}`)),
      h('div', null, `${plural(wk.count, 'application')} · ${status}`)));
    hit.addEventListener('mouseleave', hideTip);
    g.append(hit);
    root.append(g);
  });
  return root;
}

// ------------------------------------------------------------------ bot activity

const LEVEL_ICON = { success: 'check', skip: 'minus', warn: 'alert', error: 'x', info: 'info', debug: 'dot' };

async function renderActivity(root, token) {
  fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const { runs } = await api('GET', '/api/runs');
  if (stale(token)) return;

  const head = h('div', { class: 'view-head' },
    h('div', null, h('h1', null, 'Bot activity'),
      h('p', null, 'Every run, what it looked at, and why each internship was added or skipped.')),
    S.me.can_run ? h('button', { class: 'btn primary', type: 'button', disabled: !!S.status?.running, onclick: startRun }, icon('play', 'i'), 'Run bot now')
      : h('span', { class: 'small muted' }, S.me.cloud ? `The bot runs ${S.me.run_schedule} for every account.` : ''));

  if (!runs.length) {
    fill(root, head, h('div', { class: 'card empty' },
      h('h2', null, 'No runs yet'),
      h('p', null, 'Start a run with the button above, or from a terminal with ', h('code', null, 'python run.py'), '. Each step shows up here live.')));
    return;
  }
  const selectedId = S.param || runs[0].id;
  const selected = runs.find((r) => r.id === selectedId) || runs[0];

  const list = h('div', { class: 'card run-list', role: 'list' }, runs.map((r) => {
    const c = r.counts || {};
    return h('button', {
      class: 'run-item', type: 'button', role: 'listitem', 'aria-current': String(r.id === selected.id),
      onclick: () => go(`#/activity/${r.id}`),
    },
    h('div', { class: 'top' }, h('span', { class: 'when' }, `${fmtDate(r.started_at)} · ${fmtTime(r.started_at).slice(0, 5)}`),
      h('span', { class: `run-status ${r.status}` }, r.status === 'ok' ? 'OK' : r.status)),
    h('div', { class: 'meta' }, `#${r.id} · ${r.trigger} · ${r.status === 'running' ? 'running' : duration(r.started_at, r.finished_at)}`),
    h('div', { class: 'meta' }, `${c.new || 0} added · ${c.skipped || 0} skipped · ${c.fetch_errors || 0} errors`));
  }));

  const detail = h('div', { class: 'card card-pad' });
  fill(root, head, h('div', { class: 'activity' }, list, detail));
  buildRunDetail(detail, selected, token);
}

function buildRunDetail(host, run, token) {
  const tabState = store.get('activityTab', 'log');
  let showProgress = store.get('showProgress', false);
  let levelFilter = 'all';
  let lastEventId = 0;
  const events = [];

  const statsHost = h('div');
  const logHost = h('div', { class: 'log', role: 'log', 'aria-live': 'polite' });
  const panel = h('div');

  const renderStats = (r) => {
    const c = r.counts || {};
    const stat = (v, l) => h('div', { class: 'run-stat' }, h('div', { class: 'v num' }, v ?? 0), h('div', { class: 'l' }, l));
    const reasons = Object.entries(c.skip_reasons || {}).sort((a, b) => b[1] - a[1]);
    const maxR = Math.max(1, ...reasons.map(([, n]) => n));
    const prog = h('i');
    prog.style.width = `${r.companies_total ? (r.companies_done / r.companies_total) * 100 : 3}%`;
    fill(statsHost,
      h('div', { class: 'card-head' },
        h('div', null,
          h('h2', null, `Run #${r.id} `, h('span', { class: `run-status ${r.status}` }, r.status === 'ok' ? 'OK' : r.status)),
          h('p', null, `${r.trigger} · started ${fmtDate(r.started_at, true)} ${fmtTime(r.started_at)} · ${r.status === 'running' ? `running for ${duration(r.started_at)}` : `took ${duration(r.started_at, r.finished_at)}`}${c.requests ? ` · ${c.requests} HTTP requests` : ''}`))),
      r.status === 'running' ? h('div', null,
        h('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': r.companies_total, 'aria-valuenow': r.companies_done }, prog),
        h('div', { class: 'small muted' }, `${r.companies_done} of ${r.companies_total} companies done`)) : null,
      r.error ? h('div', { class: 'error-box', style: { margin: '10px 0' } }, r.error) : null,
      h('div', { class: 'run-stats', style: { marginTop: '12px' } },
        stat(c.postings, 'Postings scanned'), stat(c.internships, 'Internships'), stat(c.new, 'Added'),
        stat(c.updated, 'Updated'), stat(c.unchanged, 'Already tracked'), stat(c.skipped, 'Skipped'),
        stat(c.closed, 'Closed'), stat(c.fetch_errors, 'Fetch errors')),
      reasons.length ? h('div', { style: { marginTop: '16px' } },
        h('h3', { style: { marginBottom: '8px' } }, 'Why internships were skipped'),
        h('div', { class: 'hbars' }, reasons.map(([k, n]) => {
          const bar = h('div', { class: 'bar' });
          bar.style.width = `${(n / maxR) * 100}%`;
          return h('div', { class: 'hbar' }, h('span', { class: 'name' }, S.meta.reason_labels[k] || k), h('span', { class: 'track' }, bar, h('span', { class: 'v' }, n)));
        }))) : null,
    );
  };

  const eventRow = (ev) => h('div', { class: `log-row ${ev.level}` },
    h('span', { class: 't' }, fmtTime(ev.at)),
    icon(LEVEL_ICON[ev.level] || 'dot', 'i'),
    h('span', { class: 'm' },
      ev.job_id ? h('a', { href: `#/applications/${ev.job_id}` }, ev.message)
        : ev.url ? h('a', { href: ev.url, target: '_blank', rel: 'noopener noreferrer' }, ev.message) : ev.message));
  const passes = (ev) => (showProgress || ev.level !== 'debug') && (
    levelFilter === 'all' || (levelFilter === 'added' && ['success'].includes(ev.level))
    || (levelFilter === 'skipped' && ev.level === 'skip') || (levelFilter === 'problems' && ['warn', 'error'].includes(ev.level)));
  const renderLog = () => {
    const rows = events.filter(passes).map(eventRow);
    fill(logHost, ...(rows.length ? rows : [h('div', { class: 'log-row' }, h('span'), h('span'), h('span', { class: 'm muted' }, 'No events for this filter.'))]));
  };
  const appendEvents = (evs) => {
    const atBottom = logHost.scrollHeight - logHost.scrollTop - logHost.clientHeight < 40;
    for (const ev of evs) { events.push(ev); lastEventId = Math.max(lastEventId, ev.id); }
    const rows = evs.filter(passes).map(eventRow);
    if (rows.length) {
      if (logHost.querySelector('.m.muted')) fill(logHost, );
      logHost.append(...rows);
      if (atBottom) logHost.scrollTop = logHost.scrollHeight;
    }
  };

  const logPanel = () => {
    const chip = (key, label) => h('button', { class: 'chip', type: 'button', 'aria-pressed': String(levelFilter === key),
      onclick: (e) => { levelFilter = key; e.target.parentElement.querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', String(c === e.target))); renderLog(); } }, label);
    return h('div', null,
      h('div', { class: 'toolbar' },
        h('div', { class: 'chips', style: { margin: 0 } }, chip('all', 'All'), chip('added', 'Added'), chip('skipped', 'Skipped'), chip('problems', 'Problems')),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: showProgress, onchange: (e) => { showProgress = e.target.checked; store.set('showProgress', showProgress); renderLog(); } }), 'Show progress details')),
      logHost);
  };

  const companiesPanel = (r) => {
    const comps = (r.counts?.companies || []).slice().sort((a, b) => a.company.localeCompare(b.company));
    if (!comps.length) return h('p', { class: 'muted' }, r.status === 'running' ? 'Companies appear here as they finish.' : 'No per-company data for this run.');
    return h('div', { class: 'table-wrap' }, h('table', { class: 'mini-table' },
      h('thead', null, h('tr', null, h('th', null, 'Company'), h('th', null, 'ATS'), h('th', { class: 'num' }, 'Postings'),
        h('th', { class: 'num' }, 'Internships'), h('th', { class: 'num' }, 'Kept'), h('th', { class: 'num' }, 'Time'), h('th', null, 'Result'))),
      h('tbody', null, comps.map((c) => h('tr', null,
        h('td', null, c.company), h('td', { class: 'muted' }, c.ats), h('td', { class: 'num' }, c.postings),
        h('td', { class: 'num' }, c.internships), h('td', { class: 'num' }, c.kept), h('td', { class: 'num muted' }, `${c.seconds}s`),
        h('td', { class: c.ok ? (c.message ? '' : 'ok-text') : 'err-text' }, c.ok ? (c.message || 'OK') : c.message))))));
  };

  const skippedPanel = async () => {
    const wrap = h('div', null, h('p', { class: 'muted' }, 'Loading…'));
    try {
      const { skipped } = await api('GET', '/api/skipped');
      if (!skipped.length) { fill(wrap, h('p', { class: 'muted' }, 'No skipped internships yet.')); return wrap; }
      fill(wrap,
        h('p', { class: 'small muted', style: { marginTop: 0 } }, 'Internships the filters rejected. If one is wrong, track it anyway; to stop it happening again, adjust the phrases on the Settings page.'),
        h('div', { class: 'table-wrap' }, h('table', { class: 'mini-table' },
          h('thead', null, h('tr', null, h('th', null, 'Internship'), h('th', null, 'Reason'), h('th', null, 'Last seen'), h('th', null, ''))),
          h('tbody', null, skipped.map((x) => h('tr', null,
            h('td', null, h('div', { style: { fontWeight: 600 } }, x.role), h('div', { class: 'small muted' }, x.company)),
            h('td', null, h('div', null, S.meta.reason_labels[x.reason] || x.reason), h('div', { class: 'small muted' }, x.detail)),
            h('td', { class: 'small muted', style: { whiteSpace: 'nowrap' } }, ago(x.last_seen)),
            h('td', { style: { whiteSpace: 'nowrap' } },
              h('a', { class: 'btn sm ghost', href: x.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open ↗'),
              h('button', { class: 'btn sm', type: 'button', disabled: !!S.meta.demo, onclick: async (e) => {
                e.target.disabled = true;
                try {
                  const { job_id } = await api('POST', '/api/skipped/track', { url_key: x.url_key });
                  toast('Added to your tracker');
                  go(`#/applications/${job_id}`);
                } catch (err) { toast(err.message, true); e.target.disabled = false; }
              } }, 'Track anyway'))))))));
    } catch (err) {
      fill(wrap, h('p', { class: 'err-text' }, err.message));
    }
    return wrap;
  };

  let current = run;
  const tabs = [['log', 'Live log'], ['companies', 'Companies'], ['skipped', 'Skipped listings']];
  const tabBar = h('div', { class: 'subtabs', role: 'tablist' });
  const showTab = async (key) => {
    store.set('activityTab', key);
    tabBar.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.key === key)));
    if (key === 'log') { fill(panel, logPanel()); renderLog(); logHost.scrollTop = logHost.scrollHeight; }
    else if (key === 'companies') fill(panel, companiesPanel(current));
    else fill(panel, await skippedPanel());
  };
  tabBar.append(...tabs.map(([k, l]) => h('button', { type: 'button', role: 'tab', 'data-key': k, onclick: () => showTab(k) }, l)));

  fill(host, statsHost, h('div', { style: { marginTop: '18px' } }, tabBar, panel));
  renderStats(run);

  const load = async () => {
    if (stale(token)) return;
    try {
      const data = await api('GET', `/api/runs/${run.id}/events?after=${lastEventId}`);
      if (stale(token)) return;
      const wasRunning = current.status === 'running';
      current = data.run;
      renderStats(current);
      appendEvents(data.events);
      const activeTab = tabBar.querySelector('[aria-selected="true"]')?.dataset.key;
      if (activeTab === 'companies') fill(panel, companiesPanel(current));
      if (current.status === 'running') setTimeout(load, 1000);
      else if (wasRunning) { pollStatus(); setTimeout(() => { if (!stale(token)) renderView(); }, 400); }
    } catch {
      if (current.status === 'running') setTimeout(load, 3000);
    }
  };
  showTab(tabState).then(load);
}

// ------------------------------------------------------------------ companies

async function renderCompanies(root, token) {
  fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const data = await api('GET', '/api/companies');
  if (stale(token)) return;
  let rows = data.companies.map((c) => ({ ...c }));
  const atsTypes = data.ats_types;
  const errorBox = h('div', { class: 'error-box', hidden: true });
  const saveBtn = h('button', { class: 'btn primary', type: 'button', disabled: true }, 'Save companies');
  const discardBtn = h('button', { class: 'btn ghost', type: 'button', disabled: true }, 'Discard changes');
  const tbody = h('tbody');

  const setDirty = (dirty) => {
    S.dirtyView = dirty ? 'companies' : null;
    saveBtn.disabled = !dirty;
    discardBtn.disabled = !dirty;
  };
  const stateCell = (c) => {
    const st = c.state;
    if (c._test) return h('td', { class: 'state ' + (c._test.ok ? 'ok-text' : 'err-text') }, c._test.text);
    if (!st) return h('td', { class: 'state muted' }, 'Not fetched yet');
    return h('td', { class: 'state' },
      h('div', { class: st.ok ? '' : 'err-text' }, st.ok ? `${st.postings} postings · ${st.internships} internships · ${st.kept} kept` : st.message),
      h('div', { class: 'muted' }, `${ago(st.last_fetch_at)} · ${c.tracked} tracked${st.ok && st.message ? ` · ${st.message}` : ''}`));
  };
  const input = (c, key, placeholder, type = 'text') => h('input', {
    class: 'input', type, value: c[key] || '', placeholder, 'aria-label': `${c.name || 'New company'} ${key}`,
    oninput: (e) => { c[key] = e.target.value; setDirty(true); },
  });
  const draw = () => {
    fill(tbody, ...rows.map((c, i) => h('tr', null,
      h('td', null, h('input', { type: 'checkbox', checked: c.enabled !== 'no', 'aria-label': `Fetch ${c.name}`, style: { accentColor: 'var(--accent)' },
        onchange: (e) => { c.enabled = e.target.checked ? 'yes' : 'no'; setDirty(true); } })),
      h('td', null, input(c, 'name', 'Company')),
      h('td', null, input(c, 'category', 'e.g. YC startup')),
      h('td', null, h('select', { class: 'select', 'aria-label': `${c.name} ATS`, onchange: (e) => { c.ats = e.target.value; setDirty(true); } },
        atsTypes.map((a) => h('option', { value: a, selected: c.ats === a }, a)))),
      h('td', null, input(c, 'board_token', 'board token')),
      h('td', null, input(c, 'website', 'https://…', 'url')),
      h('td', null, input(c, 'careers_url', 'https://…', 'url')),
      h('td', null, h('select', { class: 'select', 'aria-label': `${c.name} AI screening`, onchange: (e) => { c.has_ai_screening = e.target.value; setDirty(true); } },
        ['Unknown', 'Yes', 'No'].map((a) => h('option', { value: a, selected: c.has_ai_screening === a }, a)))),
      stateCell(c),
      h('td', { style: { whiteSpace: 'nowrap' } },
        h('button', { class: 'btn sm', type: 'button', onclick: async (e) => {
          e.target.disabled = true;
          c._test = { ok: true, text: 'Checking…' };
          draw();
          try {
            const r = await api('POST', '/api/companies/test', c);
            c._test = r.ok ? { ok: true, text: `Works: ${r.postings} postings, ${r.internships} internships${r.message ? `. ${r.message}` : ''}` } : { ok: false, text: r.message };
          } catch (err) { c._test = { ok: false, text: err.message }; }
          draw();
        } }, 'Test'),
        h('button', { class: 'btn sm ghost danger', type: 'button', 'aria-label': `Remove ${c.name}`, onclick: () => { rows.splice(i, 1); setDirty(true); draw(); } }, 'Remove')))));
  };

  saveBtn.onclick = async () => {
    errorBox.hidden = true;
    saveBtn.disabled = true;
    try {
      const res = await api('PUT', '/api/companies', { companies: rows.map(({ state, tracked, _test, ...r }) => r) });
      rows = res.companies.map((c) => ({ ...c }));
      setDirty(false);
      draw();
      toast(S.me.cloud ? 'Companies saved for every account' : 'Saved to companies.csv');
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      saveBtn.disabled = false;
    }
  };
  discardBtn.onclick = () => { setDirty(false); renderView(); };
  const savedWhere = S.me.cloud ? 'Shared by every account.' : 'Saved straight to companies.csv.';

  const feedCard = h('div', { class: 'card card-pad', style: { marginBottom: '16px' } },
    h('div', { class: 'card-head' }, h('div', null, h('h2', null, 'Job feeds'),
      h('p', null, 'Remote job boards that list roles from any company. Jobs from companies not on the whitelist get a "check the company" warning.')),
    data.can_edit ? h('a', { class: 'btn sm', href: '#/settings' }, 'Configure') : null),
    h('table', { class: 'mini-table' },
      h('thead', null, h('tr', null, h('th', null, 'Feed'), h('th', null, 'Status'), h('th', null, 'Last fetch'), h('th', { class: 'num' }, 'Tracked'))),
      h('tbody', null, data.feeds.map((fd) => h('tr', null,
        h('td', null, h('div', { style: { fontWeight: 600 } }, fd.name),
          h('div', { class: 'small muted' }, `At most every ${fd.config.min_hours_between_fetches ?? 6} h${fd.ats === 'remotive' ? " (Remotive's API terms)" : ''}`)),
        h('td', { class: fd.enabled ? 'ok-text' : 'muted' }, fd.enabled ? 'On' : 'Off'),
        h('td', { class: 'small' }, fd.state
          ? h('div', null, h('div', { class: fd.state.ok ? '' : 'err-text' }, fd.state.ok ? `${fd.state.postings} postings · ${fd.state.internships} matching · ${fd.state.kept} kept` : fd.state.message),
            h('div', { class: 'muted' }, ago(fd.state.last_fetch_at)))
          : h('span', { class: 'muted' }, 'Not fetched yet')),
        h('td', { class: 'num' }, fd.tracked))))),
    h('p', { class: 'small muted', style: { margin: '10px 0 0' } },
      data.include_junior_roles ? 'Junior / entry-level roles from feeds are included.' : 'Only internships are taken from feeds. Turn on "Include junior roles" in Settings to also see junior / entry-level roles.'));

  if (!data.can_edit) {
    fill(root,
      h('div', { class: 'view-head' }, h('div', null, h('h1', null, 'Companies'),
        h('p', null, `${rows.length} vetted companies whose job boards the bot checks for everyone. Ask the admin to add one.`))),
      feedCard,
      h('div', { class: 'card' }, h('div', { class: 'table-wrap' }, h('table', { class: 'mini-table' },
        h('thead', null, h('tr', null, ['Company', 'Category', 'Board', 'AI screening', 'Last fetch', 'Your jobs'].map((t) => h('th', null, t)))),
        h('tbody', null, rows.map((c) => h('tr', null,
          h('td', null, c.website ? h('a', { href: c.website, target: '_blank', rel: 'noopener noreferrer' }, c.name) : c.name),
          h('td', { class: 'muted' }, c.category), h('td', { class: 'muted' }, `${c.ats} · ${c.board_token}`),
          h('td', null, c.has_ai_screening),
          h('td', { class: 'small' }, c.state ? (c.state.ok ? `${c.state.postings} postings · ${ago(c.state.last_fetch_at)}` : c.state.message) : 'Not fetched yet'),
          h('td', { class: 'num' }, c.tracked))))))));
    return;
  }

  fill(root,
    h('div', { class: 'view-head' },
      h('div', null, h('h1', null, 'Companies'),
        h('p', null, `${rows.length} companies on the whitelist. ${savedWhere}`)),
      h('button', { class: 'btn', type: 'button', onclick: () => {
        rows.push({ name: '', category: '', ats: 'greenhouse', board_token: '', website: '', careers_url: '', has_ai_screening: 'Unknown', enabled: 'yes', tracked: 0 });
        setDirty(true); draw();
        const inputs = tbody.querySelectorAll('tr:last-child input.input');
        if (inputs[0]) inputs[0].focus();
      } }, '+ Add company')),
    h('div', { class: 'banner' }, icon('info', 'i'), h('div', null,
      'The board token is the part of the company’s job-board URL after the domain: ',
      h('code', null, 'boards.greenhouse.io/TOKEN'), ', ', h('code', null, 'jobs.lever.co/TOKEN'), ', ',
      h('code', null, 'jobs.ashbyhq.com/TOKEN'), ', ', h('code', null, 'apply.workable.com/TOKEN'),
      '. Use Test to check it before saving. Set AI screening to Yes and every role from that company gets flagged.')),
    feedCard,
    errorBox,
    h('div', { class: 'card' },
      h('div', { class: 'table-wrap' }, h('table', { class: 'edit-table' },
        h('thead', null, h('tr', null, ['On', 'Name', 'Category', 'ATS', 'Board token', 'Website', 'Careers page', 'AI screening', 'Last fetch', ''].map((t) => h('th', null, t)))),
        tbody)),
      h('div', { class: 'sticky-actions' }, discardBtn, saveBtn)),
  );
  draw();
}

// ------------------------------------------------------------------ settings

const SETTINGS_GROUPS = [
  ['about', 'About you', 'Used in drafted notes and prefilled answers.', ['name', 'preferred_name', 'contact', 'location']],
  ['skills', 'Skills', 'Weight (1-3) is how much a match counts. Aliases are other spellings the bot should recognise, comma-separated.', ['skills']],
  ['projects', 'Projects', 'The bot links the project whose tags best match each posting. Add URLs so notes include the link.', ['projects']],
  ['filters', 'Filters', 'What gets kept or skipped. Phrase lists: one per line.', ['filters']],
  ['ai', 'AI screening terms', 'Postings that mention any of these get the AI screening flag. One per line.', ['ai_screening_terms']],
  ['drafts', 'Drafts & goals', 'Defaults for prefilled answers, follow-ups, and your weekly target.', ['drafts', 'goals']],
  ['advanced', 'Advanced', 'Red-flag thresholds and optional LLM scoring.', ['red_flags', 'llm_scoring']],
];
const SOURCE_GROUP = ['sources', 'Shared sources (admin)', 'Job feeds and fetching settings used for every account: turn feeds on or off and set their keywords. Only admins see this.', ['__sources']];
const LIST_TEMPLATES = {
  skills: { name: '', weight: 2, aliases: [] },
  projects: { name: '', url: '', summary: '', tags: [] },
  boards: { name: '', url: '' },
};
const HINTS = {
  hours_min: 'Fewer hours than this is labelled "under target".',
  hours_max: 'More than this is labelled "slightly over".',
  min_score: 'Internships scoring below this (0-100) are skipped.',
  require_paid: 'Unpaid roles are still kept, but flagged for manual review.',
  remote_only: 'Skip anything on-site or hybrid.',
  night_shift_ok: 'Mention that you can work US hours in drafts.',
  allowed_regions: 'Location words that mean you are eligible.',
  reject_phrases: 'If a posting contains any of these, it is skipped (shown under Bot activity → Skipped listings).',
  location_reject_terms: 'Skipped when these appear in the location field.',
  us_hours_phrases: 'Roles mentioning these get the "night shift compatible" badge.',
  weekly_min: 'Lower edge of your weekly goal band.',
  weekly_max: 'Upper edge of your weekly goal band.',
  followup_days: 'When you mark a job Applied, the follow-up date is set this many days later.',
  new_domain_days: 'Company domains younger than this are a red flag.',
  blocked_companies: 'Companies you marked as not real employers (pay-to-train, fake). Their listings are red-flagged. Remove a name to unblock.',
  min_delay_seconds: 'Minimum gap between requests to the same site.',
  max_workers: 'Companies fetched in parallel (each site still gets at most one request per delay).',
  user_agent: 'Identifies the bot to job boards.',
  include_junior_roles: 'Also take junior / entry-level roles (not just internships) from the job feeds.',
  onsite_ok_locations: 'On-site or hybrid roles in these places are kept instead of skipped (e.g. Pune). One per line.',
  months: 'How many monthly "Who is hiring?" threads to read (1 = current month).',
  max_new_details_per_run: 'Internshala: how many not-yet-seen internships to open per run (1 request each). The rest are read next run.',
  search_terms: 'One search per line.',
  queries: 'One search per line.',
  pages: 'Pages to read (YC: role paths like software-engineer/remote; Peerlist: number of pages).',
  boards: 'Getro-hosted portfolio job boards (name + URL, e.g. https://jobs.techstars.com).',
  repos: 'GitHub repos with a .github/scripts/listings.json file (SimplifyJobs format).',
  files: 'Raw markdown files with Company | Position | Location | Posting tables (owner/repo/branch/path).',
  min_hours_between_fetches: 'Skip this feed if it was fetched more recently than this. Keep Remotive at 6 or more (its API terms allow about 4 calls a day).',
  categories: 'We Work Remotely category feeds to read.',
};
const labelize = (k) => SOURCE_LABELS[k] || k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

function setIn(obj, path, value) {
  let o = obj;
  for (let i = 0; i < path.length - 1; i++) o = o[path[i]];
  o[path[path.length - 1]] = value;
}

async function renderSettings(root, token) {
  fill(root, h('div', { class: 'muted' }, 'Loading…'));
  const { profile, sources } = await api('GET', '/api/profile');
  if (stale(token)) return;
  let draft = structuredClone(profile);
  if (sources) draft.__sources = structuredClone(sources);
  let sourcesDirty = false;
  let raw = false;
  const errorBox = h('div', { class: 'error-box', hidden: true });
  const saveBtn = h('button', { class: 'btn primary', type: 'button', disabled: true }, 'Save settings');
  const rawBtn = h('button', { class: 'btn', type: 'button' }, 'Edit raw JSON');
  const body = h('div');
  const dirty = (on = true) => { S.dirtyView = on ? 'settings' : null; saveBtn.disabled = !on; };

  const field = (path, key, value, compact) => {
    const hint = HINTS[key] ? h('span', { class: 'hint' }, HINTS[key]) : null;
    const set = (v) => { setIn(draft, path, v); if (path[0] === '__sources') sourcesDirty = true; dirty(); };
    if (typeof value === 'boolean') {
      return h('div', { class: 'field' }, h('span', null, labelize(key)),
        h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: value, onchange: (e) => { set(e.target.checked); e.target.nextSibling.textContent = e.target.checked ? 'On' : 'Off'; } }), h('span', { class: 'small' }, value ? 'On' : 'Off')), hint);
    }
    if (typeof value === 'number') {
      return h('label', { class: 'field' }, h('span', null, labelize(key)),
        h('input', { class: 'input', type: 'number', step: 'any', value, oninput: (e) => set(e.target.value === '' ? 0 : Number(e.target.value)) }), hint);
    }
    if (typeof value === 'string' || value == null) {
      const long = (value || '').length > 70;
      return h('label', { class: 'field' + (long ? ' wide' : '') }, h('span', null, labelize(key)),
        long ? h('textarea', { class: 'textarea', rows: 2, value: value || '', oninput: (e) => set(e.target.value) })
          : h('input', { class: 'input', value: value || '', oninput: (e) => set(e.target.value) }), hint);
    }
    if (Array.isArray(value) && value.every((v) => typeof v !== 'object')) {
      if (compact) {
        return h('label', { class: 'field wide' }, h('span', null, labelize(key)),
          h('input', { class: 'input', value: value.join(', '), oninput: (e) => set(e.target.value.split(',').map((x) => x.trim()).filter(Boolean)) }), hint);
      }
      return h('label', { class: 'field wide' }, h('span', null, `${labelize(key)} `, h('span', { class: 'hint' }, `(${value.length})`)),
        h('textarea', { class: 'textarea mono', rows: Math.min(14, Math.max(3, value.length + 1)), value: value.join('\n'),
          oninput: (e) => set(e.target.value.split('\n').map((x) => x.trim()).filter(Boolean)) }), hint);
    }
    if (Array.isArray(value)) {
      const template = LIST_TEMPLATES[key] || Object.fromEntries(Object.entries(value[0] || { name: '' }).map(([k, v]) => [k, Array.isArray(v) ? [] : typeof v === 'number' ? 1 : typeof v === 'boolean' ? false : '']));
      return h('div', { class: 'field wide' },
        h('div', { class: 'obj-list' }, value.map((item, i) => h('div', { class: 'obj-item' },
          h('button', { class: 'btn sm ghost danger remove', type: 'button', onclick: () => { value.splice(i, 1); dirty(); drawForm(); } }, 'Remove'),
          h('div', { class: 'fields' }, Object.entries(item).map(([k, v]) => field([...path, i, k], k, v, true)))))),
        h('div', null, h('button', { class: 'btn sm', type: 'button', onclick: () => { value.push(structuredClone(template)); dirty(); drawForm(); } }, `+ Add ${key.replace(/s$/, '')}`)));
    }
    if (value && typeof value === 'object') {
      return h('div', { class: 'field wide' }, compact ? null : h('h3', { style: { margin: '6px 0 2px' } }, labelize(key)),
        h('div', { class: 'fields' }, Object.entries(value).map(([k, v]) => field([...path, k], k, v, compact))));
    }
    return null;
  };

  const drawForm = () => {
    const groups = [...SETTINGS_GROUPS, ...(draft.__sources ? [SOURCE_GROUP] : [])];
    const used = new Set(groups.flatMap((g) => g[3]));
    const other = Object.keys(draft).filter((k) => !used.has(k));
    if (other.length) groups.push(['other', 'Other', 'Settings without a dedicated section.', other]);
    fill(body, h('div', { class: 'settings' },
      h('nav', { class: 'settings-nav', 'aria-label': 'Settings sections' }, groups.map(([id, title]) => h('a', { href: `#/settings`, onclick: (e) => { e.preventDefault(); document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: 'smooth' }); } }, title))),
      h('div', { class: 'grid' }, groups.map(([id, title, desc, keys]) => h('section', { class: 'card settings-section', id: `set-${id}` },
        h('div', { class: 'head' }, h('h2', null, title), h('p', null, desc)),
        h('div', { class: 'fields' }, keys.filter((k) => k in draft).map((k) => {
          const v = draft[k];
          if (v && typeof v === 'object' && !Array.isArray(v)) {
            return h('div', { class: 'field wide' }, keys.length > 1 ? h('h3', { style: { margin: '6px 0 2px' } }, labelize(k)) : null,
              h('div', { class: 'fields' }, Object.entries(v).map(([kk, vv]) => field([k, kk], kk, vv))));
          }
          return field([k], k, v);
        })))))));
  };
  const drawRaw = () => {
    const ta = h('textarea', { class: 'textarea mono', rows: 40, value: JSON.stringify(draft, null, 2), spellcheck: 'false',
      oninput: (e) => { try { draft = JSON.parse(e.target.value); errorBox.hidden = true; dirty(); } catch (err) { errorBox.textContent = `Invalid JSON: ${err.message}`; errorBox.hidden = false; saveBtn.disabled = true; } } });
    fill(body, h('div', { class: 'card card-pad' }, ta));
  };
  rawBtn.onclick = () => {
    if (raw && !errorBox.hidden) return toast('Fix the JSON error first', true);
    raw = !raw;
    rawBtn.textContent = raw ? 'Back to form' : 'Edit raw JSON';
    raw ? drawRaw() : drawForm();
  };
  saveBtn.onclick = async () => {
    errorBox.hidden = true;
    saveBtn.disabled = true;
    try {
      const { __sources, ...mine } = draft;
      const res = await api('PUT', '/api/profile', { profile: mine });
      if (__sources && sourcesDirty) {
        await api('PUT', '/api/sources', { sources: __sources });
        sourcesDirty = false;
      }
      draft = { ...structuredClone(res.profile), ...(__sources ? { __sources } : {}) };
      dirty(false);
      S.meta = await api('GET', '/api/meta');
      S.me = await api('GET', '/api/me');
      const found = res.matched && res.matched.new ? ` Found ${plural(res.matched.new, 'new match', 'new matches')} from recent postings.` : '';
      toast((S.me.cloud ? 'Saved.' : 'Saved to profile.json.') + (found || ' Applies on the next bot run.'));
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      saveBtn.disabled = false;
    }
  };

  fill(root,
    h('div', { class: 'view-head' },
      h('div', null, h('h1', null, 'Settings'), h('p', null, S.me.cloud
        ? 'Your profile: skills, projects and filters the bot uses to match internships for you. Changes apply on the next run.'
        : 'Your profile.json, editable here. Changes apply on the next bot run.')),
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, h('button', {
        class: 'btn', type: 'button', title: 'Apply your current settings and red-flag rules to internships you already track',
        onclick: async (e) => {
          if (S.dirtyView === 'settings') return toast('Save your changes first', true);
          e.target.disabled = true;
          try {
            const r = await api('POST', '/api/rescore');
            const parts = [`${r.checked || 0} checked`, `${r.updated || 0} updated`];
            if (r.moved_to_skipped) parts.push(`${r.moved_to_skipped} moved to Skipped (red flag)`);
            if (r.now_filtered_out) parts.push(`${r.now_filtered_out} no longer pass your filters`);
            toast(`Re-checked: ${parts.join(' · ')}`);
          } catch (err) { toast(err.message, true); }
          e.target.disabled = false;
        } }, 'Re-check tracked internships'), rawBtn, saveBtn)),
    errorBox,
    body,
    S.me.cloud ? h('section', { class: 'card settings-section danger-zone', style: { marginTop: '16px' } },
      h('div', { class: 'head' }, h('h2', null, 'Delete my account'),
        h('p', null, 'Permanently deletes your profile, tracked internships, notes and history. This can’t be undone.')),
      h('div', null, h('button', { class: 'btn danger', type: 'button', onclick: async () => {
        if (prompt(`Type DELETE to permanently delete ${S.me.email} and all its data.`) !== 'DELETE') return;
        try { await api('DELETE', '/api/account'); location.href = '/login'; } catch (err) { toast(err.message, true); }
      } }, 'Delete my account and data'))) : null);
  drawForm();
}

// ------------------------------------------------------------------ boot

function renderUserMenu() {
  const menu = $('#userMenu');
  if (!S.me.cloud) return;
  const initial = (S.me.name || S.me.email || '?').trim()[0].toUpperCase();
  const avatar = h('span', { class: 'avatar' }, S.me.picture ? h('img', { src: S.me.picture, alt: '', referrerpolicy: 'no-referrer' }) : initial);
  const pop = h('div', { class: 'card user-pop', hidden: true },
    h('div', { class: 'who' }, h('b', null, S.me.name || S.me.email), h('span', { class: 'muted' }, S.me.email)),
    S.me.is_admin ? h('span', { class: 'role-tag' }, 'Admin: manages shared sources') : null,
    h('a', { class: 'btn sm', href: '#/settings' }, 'My profile & settings'),
    h('button', { class: 'btn sm', type: 'button', onclick: async () => {
      try { await api('POST', '/auth/logout'); } finally { location.href = '/login'; }
    } }, 'Sign out'));
  const btn = h('button', { class: 'user-btn', type: 'button', 'aria-label': 'Account menu', 'aria-expanded': 'false',
    onclick: (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; btn.setAttribute('aria-expanded', String(!pop.hidden)); } }, avatar);
  addEventListener('click', (e) => { if (!menu.contains(e.target)) pop.hidden = true; });
  fill(menu, btn, pop);
  menu.hidden = false;
}

async function boot() {
  try {
    S.me = await api('GET', '/api/me');
    S.meta = await api('GET', '/api/meta');
  } catch (err) {
    fill($('#view'), h('div', { class: 'card empty' }, h('h2', null, 'Can’t reach the tracker server'),
      h('p', null, 'Start it with python serve.py and reload this page.'), h('p', { class: 'small muted' }, err.message)));
    return;
  }
  $('#demoBadge').hidden = !S.meta.demo;
  if (S.meta.demo) $('#runBtn').title = 'Disabled in demo mode';
  $('#runBtn').hidden = S.me.cloud && !S.me.can_run;
  $('#runBtn').addEventListener('click', startRun);
  renderUserMenu();
  if (S.me.resumed) toast('Welcome back! Your daily search was paused while you were away; it’s back on.');
  $('#scrim').addEventListener('click', () => closeDrawer());
  addEventListener('hashchange', route);
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && S.drawerId) closeDrawer();
    if (e.key === '/' && S.view === 'applications' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      $('#search')?.focus();
    }
  });
  addEventListener('beforeunload', (e) => {
    if (Object.keys(S.pending).length && S.pendingId) {
      fetch(`/api/jobs/${S.pendingId}`, { method: 'PATCH', keepalive: true, headers: { 'X-Tracker': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(S.pending) });
    }
    if (S.dirtyView) { e.preventDefault(); e.returnValue = ''; }
  });
  route();
  pollStatus();
}

boot();
