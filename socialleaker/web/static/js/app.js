/* ─────────────────────────────────────────────────────────────
   Social Leaker — task-centric agent panel
   ───────────────────────────────────────────────────────────── */
'use strict';

// ── API helper ─────────────────────────────────────────────
const api = {
  async req(method, url, body) {
    const opts = { method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    const res = await fetch(url, opts);
    if (res.status === 401) { window.location.href = '/login'; throw new Error('Unauthorized'); }
    if (!res.ok) { let d = res.statusText; try { d = (await res.json()).detail || d; } catch (_) {} throw new Error(d); }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },
  get(u) { return this.req('GET', u); }, post(u, b) { return this.req('POST', u, b); },
  put(u, b) { return this.req('PUT', u, b); }, del(u) { return this.req('DELETE', u); },
};

// ── Utilities ──────────────────────────────────────────────
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
const fmtDate = (s) => { if (!s) return '—'; return new Date(s).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
const relTime = (s) => { const d = (Date.now() - new Date(s)) / 1000; if (d < 60) return 'just now'; if (d < 3600) return Math.floor(d / 60) + 'm ago'; if (d < 86400) return Math.floor(d / 3600) + 'h ago'; return Math.floor(d / 86400) + 'd ago'; };

function toast(msg, kind = '') {
  const t = el('div', 'toast ' + kind, esc(msg));
  $('#toastHost').appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3200);
}

// ── Platform icons ─────────────────────────────────────────
const IG_LOGO = `<svg viewBox="0 0 48 48" width="38" height="38" aria-hidden="true">
  <defs><radialGradient id="igg" cx="30%" cy="107%" r="130%">
    <stop offset="0" stop-color="#fdf497"/><stop offset=".05" stop-color="#fdf497"/>
    <stop offset=".45" stop-color="#fd5949"/><stop offset=".6" stop-color="#d6249f"/>
    <stop offset=".9" stop-color="#285AEB"/></radialGradient></defs>
  <rect x="3" y="3" width="42" height="42" rx="13" fill="url(#igg)"/>
  <rect x="12" y="12" width="24" height="24" rx="7" fill="none" stroke="#fff" stroke-width="2.6"/>
  <circle cx="24" cy="24" r="6.4" fill="none" stroke="#fff" stroke-width="2.6"/>
  <circle cx="31.6" cy="16.4" r="1.9" fill="#fff"/></svg>`;
const genLogo = (name) => `<div class="gen-logo">${esc((name || '?')[0].toUpperCase())}</div>`;
const platformIcon = (provider, name) => provider === 'instagram' ? IG_LOGO : genLogo(name);

// Avatar: shows the real profile picture with a letter fallback if it fails/expires.
function avatarHtml(username, pic) {
  const letter = esc(((username || '?')[0] || '?').toUpperCase());
  // Route through our proxy so Instagram/Reddit hotlink protection is bypassed;
  // if the image is gone/expired the <img> removes itself and the letter shows.
  const img = pic
    ? `<img class="pfp-img" src="/api/avatar?u=${encodeURIComponent(pic)}" alt="" loading="lazy" onerror="this.remove()">`
    : '';
  return `<div class="pfp">${img}<span>${letter}</span></div>`;
}

// ── Modal ──────────────────────────────────────────────────
function openModal(title, body, foot) {
  const m = $('#modal');
  m.innerHTML = `<div class="modal-head"><h3>${esc(title)}</h3><button class="close-x" data-close>×</button></div>
    <div class="modal-body">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ''}`;
  $('#modalBackdrop').hidden = false;
  m.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', closeModal));
  return m;
}
function closeModal() { $('#modalBackdrop').hidden = true; $('#modal').innerHTML = ''; }
$('#modalBackdrop').addEventListener('click', (e) => { if (e.target.id === 'modalBackdrop') closeModal(); });

// ── Router ─────────────────────────────────────────────────
const TITLES = { dashboard: 'Dashboard', tasks: 'Tasks', platforms: 'Platforms', settings: 'Settings' };
let currentView = 'dashboard';
let currentTaskId = null;   // set when a task detail is open (for deep-linking)

function showView(name, skipHash) {
  if (!TITLES[name]) name = 'dashboard';
  currentView = name;
  currentTaskId = null;
  if (!skipHash && location.hash.slice(1) !== name) location.hash = name;
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.view === name));
  $$('.view').forEach((v) => (v.hidden = v.dataset.view !== name));
  $('#viewTitle').textContent = TITLES[name] || name;
  ({ dashboard: loadDashboard, tasks: loadTasks, platforms: loadPlatforms, settings: loadSettings }[name] || (() => {}))();
}

// Route from the URL hash. Supports `#tasks/<id>` so a refresh restores the
// live task-detail view instead of dropping you back on the list.
function applyRoute() {
  const parts = location.hash.slice(1).split('/');
  if (parts[0] === 'tasks' && parts[1]) {
    const id = +parts[1];
    if (currentView === 'tasks' && currentTaskId === id) return;
    if (currentView !== 'tasks') {
      currentView = 'tasks';
      $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.view === 'tasks'));
      $$('.view').forEach((v) => (v.hidden = v.dataset.view !== 'tasks'));
      $('#viewTitle').textContent = 'Tasks';
    }
    openTask(id);
    return;
  }
  const view = parts[0] || 'dashboard';
  if (view === currentView && !currentTaskId) return;
  showView(view, true);
}
$$('.nav-item').forEach((n) => n.addEventListener('click', () => showView(n.dataset.view)));
window.addEventListener('hashchange', applyRoute);
document.addEventListener('click', (e) => { const g = e.target.closest('[data-goto]'); if (g) showView(g.dataset.goto); });

$('#logoutBtn').addEventListener('click', async () => { await api.post('/api/auth/logout').catch(() => {}); window.location.href = '/login'; });

// ─────────────────────────────────────────────────────────
//  DASHBOARD
// ─────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const s = await api.get('/api/stats');
    const cards = [
      { label: 'Profiles collected', value: fmt(s.profiles.total), accent: true },
      { label: 'Total reach (followers)', value: fmt(s.profiles.reach) },
      { label: 'Tasks completed', value: fmt(s.tasks.completed) },
      { label: 'Running now', value: fmt(s.tasks.running) },
      { label: 'Platforms connected', value: fmt(s.connections.platforms_connected) },
      { label: 'Agent', value: s.connections.claude_connected ? 'Connected' : 'Offline' },
    ];
    $('#statGrid').innerHTML = cards.map((c) => `<div class="stat"><div class="label">${c.label}</div>
      <div class="value ${c.accent ? 'accent' : ''}">${c.value}</div></div>`).join('');

    // recent tasks
    const rt = $('#recentTasks');
    rt.innerHTML = s.recent_tasks.length ? s.recent_tasks.map((t) => `
      <div class="mini-row" data-open-task="${t.id}">
        <div><div class="m-name">${esc(t.title)}</div>
          <div class="m-meta">${t.collected_count}/${t.goal_target} · ${t.iterations} loops · ${relTime(t.created_at)}</div></div>
        <span class="badge ${t.status}">${t.status}</span></div>`).join('')
      : `<div class="empty"><div class="em-icon">✦</div>No tasks yet.</div>`;
    rt.querySelectorAll('[data-open-task]').forEach((r) => r.addEventListener('click', () => { showView('tasks'); setTimeout(() => openTask(+r.dataset.openTask), 60); }));

    // connections
    const c = s.connections;
    const plats = c.platforms.filter((p) => p.status === 'connected');
    $('#connSummary').innerHTML = `
      <div class="conn-item">
        <div class="conn-ic">${IG_LOGO}</div>
        <div class="conn-tx"><div class="conn-name">Platforms</div>
          <div class="muted xs">${plats.length ? plats.map((p) => esc(p.label || p.provider)).join(', ') : 'None connected'}</div></div>
        <button class="btn btn-sm btn-ghost" data-goto="platforms">Manage</button>
      </div>
      <div class="conn-item">
        <div class="conn-ic claude-ic">✦</div>
        <div class="conn-tx"><div class="conn-name">Claude agent</div>
          <div class="muted xs">${c.claude_connected ? 'Connected' : 'Not connected'}</div></div>
        <button class="btn btn-sm ${c.claude_connected ? 'btn-ghost' : 'btn-primary'}" data-goto="settings">${c.claude_connected ? 'Manage' : 'Connect'}</button>
      </div>`;

    // usage
    const u = s.usage;
    $('#usageReport').innerHTML = `
      <div class="ur-cell"><div class="ur-v">${fmt(u.requests)}</div><div class="ur-l">Platform requests</div></div>
      <div class="ur-cell"><div class="ur-v">${fmt(u.iterations)}</div><div class="ur-l">Loop iterations</div></div>
      <div class="ur-cell"><div class="ur-v">${fmt(u.tokens_est)}</div><div class="ur-l">Est. agent tokens</div></div>
      <div class="ur-cell"><div class="ur-v">${fmt(s.profiles.verified)}</div><div class="ur-l">Verified profiles</div></div>`;
  } catch (e) { toast(e.message, 'bad'); }
  updateAgentBadge();
}

async function updateAgentBadge() {
  try {
    const st = await api.get('/api/integrations/claude/status');
    const b = $('#agentBadge');
    b.classList.toggle('on', st.connected);
    b.classList.toggle('off', !st.connected);
    // Professional auto-detect: if Claude Code is already logged in on this
    // machine but not yet connected here, connect it automatically (once/session).
    if (!st.connected && st.sdk_available && st.cli_logged_in && !sessionStorage.getItem('claudeAutoTried')) {
      try { sessionStorage.setItem('claudeAutoTried', '1'); } catch (_) {}
      b.title = 'Claude Code detected — connecting…';
      try {
        const r = await api.post('/api/integrations/claude/login');
        if (r.ok) {
          b.classList.add('on'); b.classList.remove('off');
          toast('Claude Code auto-connected ✓', 'good');
          if (currentView === 'settings') loadSettings();
        }
      } catch (_) {}
    }
  } catch (_) {}
}

// ─────────────────────────────────────────────────────────
//  TASKS
// ─────────────────────────────────────────────────────────
let taskPollTimer = null;

let queueSnap = { current: null, queued: [] };
async function loadTasks() {
  $('#taskDetail').hidden = true; $('#taskListWrap').hidden = false;
  try {
    const [list, snap] = await Promise.all([api.get('/api/tasks'), api.get('/api/tasks/queue/status').catch(() => ({ current: null, queued: [] }))]);
    queueSnap = snap;
    const qn = snap.queued.length + (snap.current ? 1 : 0);
    $('#taskCount').textContent = (list.length ? `${list.length} task(s)` : '') + (qn ? ` · ${qn} in queue` : '');
    const host = $('#taskList');
    host.innerHTML = list.length ? list.map(taskCard).join('')
      : `<div class="empty"><div class="em-icon">✦</div>No tasks yet.<br/>Create one and describe your goal — the agent loops until it's reached.</div>`;
    host.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', (e) => { e.stopPropagation(); taskAction(b.dataset.act, +b.dataset.id); }));
    host.querySelectorAll('.task-card').forEach((c) => c.addEventListener('click', () => openTask(+c.dataset.id)));

    const active = list.some((t) => ['running', 'queued'].includes(t.status));
    clearTimeout(taskPollTimer);
    if (active && currentView === 'tasks' && $('#taskListWrap').hidden === false) taskPollTimer = setTimeout(loadTasks, 2500);
  } catch (e) { toast(e.message, 'bad'); }
}

function progressBar(t) {
  if (t.goal_target && t.goal_target > 0) {
    const pct = Math.min(100, Math.round((t.collected_count / t.goal_target) * 100));
    return `<div class="prog"><div class="prog-fill" style="width:${pct}%"></div></div>
      <div class="prog-meta"><span>${fmt(t.collected_count)}/${fmt(t.goal_target)} collected</span><span>${t.iterations} loop(s)</span></div>`;
  }
  // No cap — show the running count without a fixed denominator.
  return `<div class="prog-meta" style="margin-top:12px"><span><b>${fmt(t.collected_count)}</b> collected · no cap</span><span>${t.iterations} loop(s)</span></div>`;
}

function queueLabel(t) {
  if (queueSnap.current === t.id) return '<span class="qpill running">running now</span>';
  const i = queueSnap.queued.indexOf(t.id);
  if (i >= 0) return `<span class="qpill">#${i + 1} in queue</span>`;
  return '';
}
function taskCard(t) {
  const running = ['running', 'queued'].includes(t.status);
  return `<div class="task-card" data-id="${t.id}">
    <div class="cc-head"><div class="tc-title"><span class="pf-mini">${platformIcon(t.platform)}</span><h4>${esc(t.title)}</h4></div>
      <div class="tc-badges">${queueLabel(t)}<span class="badge ${t.status}">${t.status}</span></div></div>
    <div class="cc-obj">${esc(t.prompt)}</div>
    ${progressBar(t)}
    <div class="cc-actions">
      ${running ? `<button class="btn btn-sm btn-danger" data-act="stop" data-id="${t.id}">■ Stop</button>`
               : `<button class="btn btn-sm btn-primary" data-act="start" data-id="${t.id}">▶ Run</button>`}
      <button class="btn btn-sm btn-ghost" data-act="open" data-id="${t.id}">Open</button>
      <button class="btn btn-sm btn-ghost" data-act="delete" data-id="${t.id}">Delete</button>
    </div></div>`;
}

async function taskAction(act, id) {
  try {
    if (act === 'start') { await api.post(`/api/tasks/${id}/start`); toast('Task started', 'good'); refreshCurrentTaskView(id); }
    else if (act === 'stop') { await api.post(`/api/tasks/${id}/stop`); toast('Stop requested'); refreshCurrentTaskView(id); }
    else if (act === 'delete') { if (!confirm('Delete this task and its results?')) return; await api.del(`/api/tasks/${id}`); toast('Task deleted'); loadTasks(); }
    else if (act === 'open') openTask(id);
  } catch (e) { toast(e.message, 'bad'); }
}
function refreshCurrentTaskView(id) { if (!$('#taskDetail').hidden) openTask(id, true); else loadTasks(); }

$('#newTaskBtn').addEventListener('click', openTaskForm);
function openTaskForm() {
  openModal('New task', `
    <label class="field"><span>Title <span class="muted">(optional)</span></span>
      <input id="t_title" placeholder="Auto-generated from the prompt if empty" /></label>
    <label class="field"><span>What do you want? (prompt / goal)</span>
      <textarea id="t_prompt" rows="4" placeholder="e.g. Profile these fashion pages and report follower counts."></textarea></label>
    <label class="field"><span>Seed handles <span class="muted">(comma-separated — recommended)</span></span>
      <input id="t_seeds" placeholder="nike, adidas, zara" /></label>
    <label class="field"><span>Platform</span>
      <select id="t_platform">
        <option value="instagram">Instagram</option>
        <option value="reddit">Reddit</option>
      </select></label>
    <label class="field"><span>Max profiles <span class="muted">(optional — leave blank to collect everything found)</span></span>
      <input id="t_goal" type="number" placeholder="all" min="1" max="2000" /></label>
    <div class="hintbox">Give <b>seed handles</b> to collect known pages (no login needed). Leave them blank and just describe a topic — the task uses <b>Claude + partitioned search</b> (many sub-queries) to discover as many public pages as it can, then collects them all. Topic discovery needs Claude connected (Settings) and/or an Instagram <b>Session ID</b> (Platforms).</div>
  `, `<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" id="saveTask">Create &amp; run</button>`);

  $('#saveTask').addEventListener('click', async () => {
    let prompt = $('#t_prompt').value.trim();
    const seeds = $('#t_seeds').value.trim();
    if (!prompt && !seeds) { toast('Describe what you want or add seed handles', 'bad'); return; }
    // Fold seed handles into the prompt as @mentions so the runner picks them up.
    if (seeds) {
      const tags = seeds.split(/[,\n; ]+/).filter(Boolean).map((h) => '@' + h.replace(/^@/, '')).join(' ');
      prompt = (prompt ? prompt + '\n\nTargets: ' : 'Collect these profiles: ') + tags;
    }
    const payload = { title: $('#t_title').value.trim() || null, prompt, platform: $('#t_platform').value,
      goal_target: +$('#t_goal').value || 0, max_iterations: 12 };
    try {
      const task = await api.post('/api/tasks', payload);
      await api.post(`/api/tasks/${task.id}/start`);
      closeModal(); toast('Task created & running', 'good'); showView('tasks'); openTask(task.id);
    } catch (e) { toast(e.message, 'bad'); }
  });
}

// ── Task detail (step-by-step) ─────────────────────────────
let detailTimer = null, detailLastSeq = 0, detailResultsTimer = null;
async function openTask(id, silent) {
  clearTimeout(taskPollTimer);
  currentTaskId = id;
  if (location.hash.slice(1) !== 'tasks/' + id) location.hash = 'tasks/' + id;
  const t = await api.get(`/api/tasks/${id}`).catch((e) => { toast(e.message, 'bad'); });
  if (!t) return;
  $('#taskListWrap').hidden = true;
  const d = $('#taskDetail'); d.hidden = false;
  const running = ['running', 'queued'].includes(t.status);
  d.innerHTML = `
    <div class="detail-top">
      <button class="btn btn-ghost btn-sm" id="backTasks">← Tasks</button>
      <div class="spacer"></div>
      ${running ? `<button class="btn btn-danger btn-sm" id="d_stop">■ Stop</button>`
               : `<button class="btn btn-primary btn-sm" id="d_run">▶ Run again</button>`}
      <button class="btn btn-ghost btn-sm" id="d_csv">Export CSV</button>
      <button class="btn btn-ghost btn-sm" id="d_report">Report JSON</button>
    </div>
    <div class="detail-head panel">
      <div class="dh-main">
        <div class="tc-title"><span class="pf-mini">${platformIcon(t.platform)}</span><h2>${esc(t.title)}</h2>
          <span class="badge ${t.status}" id="d_status">${t.status}</span></div>
        <p class="muted dh-prompt">${esc(t.prompt)}</p>
        ${progressBar(t)}
      </div>
      <div class="dh-usage">
        <div><span class="u-v" id="u_iter">${fmt(t.iterations)}</span><span class="u-l">iterations</span></div>
        <div><span class="u-v" id="u_req">${fmt(t.requests_count)}</span><span class="u-l">requests</span></div>
        <div><span class="u-v" id="u_tok">${fmt(t.tokens_est)}</span><span class="u-l">est. tokens</span></div>
      </div>
    </div>
    <div class="detail-split">
      <div class="panel steps-panel">
        <div class="panel-head"><h3>Steps</h3><span class="conn-pill ${running ? 'live' : ''}" id="d_live">${running ? 'live' : 'idle'}</span></div>
        <div class="steps" id="stepList"></div>
      </div>
      <div class="panel results-panel">
        <div class="panel-head"><h3>Collected</h3><span class="muted xs" id="resCount"></span></div>
        <div class="res-scroll"><table class="table" id="detailResults"><thead>
          <tr><th>Profile</th><th class="num">Followers</th><th>Flags</th></tr></thead><tbody></tbody></table></div>
      </div>
    </div>
    <div class="followup panel">
      <div class="panel-head"><h3>Add a follow-up instruction</h3></div>
      <div class="followup-body">
        <input id="d_followup" placeholder="e.g. also expand from @newpage and collect 10 more…" />
        <button class="btn btn-primary" id="d_followSend">Queue follow-up ▸</button>
      </div>
      <div class="muted xs followup-note">The follow-up is appended to this task and re-queued — it runs after the current queue clears.</div>
    </div>`;

  $('#backTasks').addEventListener('click', () => { clearTimeout(detailTimer); clearTimeout(detailResultsTimer); showView('tasks'); });
  const runBtn = $('#d_run'); if (runBtn) runBtn.addEventListener('click', async () => { await taskAction('start', id); });
  const stopBtn = $('#d_stop'); if (stopBtn) stopBtn.addEventListener('click', async () => { await taskAction('stop', id); });
  $('#d_csv').addEventListener('click', () => window.open(`/api/results/export?fmt=csv&task_id=${id}`, '_blank'));
  $('#d_report').addEventListener('click', () => window.open(`/api/tasks/${id}/report`, '_blank'));
  $('#d_followSend').addEventListener('click', async () => {
    const text = $('#d_followup').value.trim();
    if (!text) { toast('Write a follow-up first', 'bad'); return; }
    try { await api.post(`/api/tasks/${id}/followup`, { text }); $('#d_followup').value = ''; toast('Follow-up queued', 'good'); openTask(id, true); }
    catch (e) { toast(e.message, 'bad'); }
  });

  detailLastSeq = 0;
  pumpSteps(id); pumpResults(id);
}

async function pumpSteps(id) {
  try {
    const steps = await api.get(`/api/tasks/${id}/steps?after_seq=${detailLastSeq}`);
    const box = $('#stepList'); if (!box) { clearTimeout(detailTimer); return; }
    steps.forEach((s) => { detailLastSeq = Math.max(detailLastSeq, s.seq); box.appendChild(stepRow(s)); });
    if (steps.length) box.scrollTop = box.scrollHeight;
  } catch (_) {}
  // refresh header status/usage
  try {
    const t = await api.get(`/api/tasks/${id}`);
    const badge = $('#d_status'); if (badge) { badge.className = 'badge ' + t.status; badge.textContent = t.status; }
    const set = (sel, v) => { const n = $(sel); if (n) n.textContent = fmt(v); };
    set('#u_iter', t.iterations); set('#u_req', t.requests_count); set('#u_tok', t.tokens_est);
    const running = ['running', 'queued'].includes(t.status);
    const live = $('#d_live'); if (live) { live.textContent = running ? 'live' : 'idle'; live.classList.toggle('live', running); }
    if ($('#stepList')) detailTimer = setTimeout(() => { if ($('#stepList')) pumpSteps(id); }, running ? 1500 : 4000);
  } catch (_) {}
}

const PHASE_ICON = { plan: '◆', collect: '⬇', expand: '⤢', reflect: '◈', done: '✓', error: '✕' };
function stepRow(s) {
  const icon = PHASE_ICON[s.phase] || '•';
  return el('div', `step ${s.phase} ${s.status}`, `
    <div class="step-ic">${icon}</div>
    <div class="step-body">
      <div class="step-title">${esc(s.title)} <span class="step-time">${fmtDate(s.created_at)}</span></div>
      ${s.detail ? `<div class="step-detail">${esc(s.detail)}</div>` : ''}
    </div>`);
}

async function pumpResults(id) {
  try {
    const rows = await api.get(`/api/results?task_id=${id}&limit=300&sort=followers`);
    const tb = $('#detailResults tbody'); if (!tb) { clearTimeout(detailResultsTimer); return; }
    $('#resCount').textContent = rows.length ? `${rows.length}` : '';
    tb.innerHTML = rows.length ? rows.map((p) => `<tr>
      <td><div class="profile-cell">${avatarHtml(p.username, p.profile_pic_url)}
        <div class="handle">@${esc(p.username)}<br/><small>${esc(p.full_name || '')}</small></div></div></td>
      <td class="num">${fmt(p.followers)}</td>
      <td>${p.is_verified ? '<span class="badge completed mini">verified</span> ' : ''}${p.is_private ? '<span class="badge draft mini">private</span>' : ''}</td>
    </tr>`).join('') : `<tr><td colspan="3"><div class="empty small">Nothing collected yet.</div></td></tr>`;
  } catch (_) {}
  const t = await api.get(`/api/tasks/${id}`).catch(() => null);
  const running = t && ['running', 'queued'].includes(t.status);
  if ($('#detailResults')) detailResultsTimer = setTimeout(() => { if ($('#detailResults')) pumpResults(id); }, running ? 2000 : 6000);
}

// ─────────────────────────────────────────────────────────
//  PLATFORMS
// ─────────────────────────────────────────────────────────
async function loadPlatforms() {
  try {
    const [cat, mine] = await Promise.all([api.get('/api/integrations/catalog'), api.get('/api/integrations')]);
    const byProvider = {}; mine.forEach((m) => (byProvider[m.provider] = m));
    $('#platformGrid').innerHTML = cat.platforms.map((p) => {
      const conn = byProvider[p.provider];
      const connected = conn && conn.status === 'connected';
      let body;
      if (!p.available) {
        body = `<span class="badge draft">coming soon</span>`;
      } else if (p.auth === 'none') {
        body = `<span class="badge completed">ready · no login</span>`;
      } else if (connected) {
        body = `<span class="badge completed">connected</span>
               <div class="pc-sub muted xs">${esc(conn.label || '')}</div>
               <button class="btn btn-sm btn-ghost" data-disc="${p.provider}">Disconnect</button>`;
      } else {
        body = `<span class="badge draft">optional login</span>
               <button class="btn btn-sm btn-primary" data-conn="${p.provider}">Connect</button>`;
      }
      return `<div class="platform-card ${p.available ? '' : 'soon'}">
        <div class="pc-logo">${platformIcon(p.provider, p.name)}</div>
        <div class="pc-name">${esc(p.name)}</div>
        ${body}
        <div class="pc-note muted xs">${esc(p.note || '')}</div>
      </div>`;
    }).join('');
    $('#platformGrid').querySelectorAll('[data-conn]').forEach((b) => b.addEventListener('click', () => connectPlatformModal(b.dataset.conn)));
    $('#platformGrid').querySelectorAll('[data-disc]').forEach((b) => b.addEventListener('click', async () => {
      try { await api.post(`/api/integrations/${b.dataset.disc}/disconnect`); toast('Disconnected'); loadPlatforms(); } catch (e) { toast(e.message, 'bad'); }
    }));
  } catch (e) { toast(e.message, 'bad'); }
}

function connectPlatformModal(provider) {
  const cap = provider.charAt(0).toUpperCase() + provider.slice(1);
  openModal(`Connect ${cap}`, `
    <div class="modal-logo">${platformIcon(provider, provider)}</div>
    <div class="seg" id="igSeg">
      <button class="seg-btn active" data-m="session" type="button">Session ID · recommended</button>
      <button class="seg-btn" data-m="password" type="button">Username &amp; password</button>
    </div>

    <div id="m_session">
      <div class="hintbox">Most reliable method. Log in to <b>instagram.com</b> in your browser, open
        DevTools → <b>Application → Cookies</b> → copy the value of the <code>sessionid</code> cookie
        and paste it here. This reuses your real session and avoids Instagram's login blocks.</div>
      <label class="field"><span>sessionid cookie</span>
        <input id="ig_sess" placeholder="e.g. 71234567%3AAbCdEf..." autocomplete="off" /></label>
    </div>

    <div id="m_password" hidden>
      <div class="hintbox err">Instagram frequently blocks username/password logins from new
        accounts or VPN/new IPs with an <b>“out of date”</b> error and sends no code. If that
        happens, use <b>Session ID</b> instead.</div>
      <label class="field"><span>Username</span><input id="p_user" placeholder="account username" autocomplete="off" /></label>
      <label class="field"><span>Password</span><input id="p_pass" type="password" placeholder="••••••••" autocomplete="off" /></label>
      <div id="p_2fa" hidden>
        <label class="field"><span id="p_2fa_label">Verification code</span>
          <input id="p_code" inputmode="numeric" placeholder="6-digit code" autocomplete="one-time-code" /></label>
      </div>
    </div>

    <div id="p_msg"></div>
  `, `<button class="btn btn-ghost" data-close>Cancel</button><button class="btn btn-primary" id="doConnect">Connect</button>`);

  const msg = (html, err) => { $('#p_msg').innerHTML = html ? `<div class="hintbox ${err ? 'err' : ''}">${html}</div>` : ''; };
  const btn = $('#doConnect');
  let mode = 'session';
  let awaitingCode = false;

  $$('#igSeg .seg-btn').forEach((b) => b.addEventListener('click', () => {
    mode = b.dataset.m; awaitingCode = false; msg('');
    $$('#igSeg .seg-btn').forEach((x) => x.classList.toggle('active', x === b));
    $('#m_session').hidden = mode !== 'session';
    $('#m_password').hidden = mode !== 'password';
    $('#p_2fa').hidden = true;
    btn.textContent = 'Connect';
  }));

  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Working…';
    try {
      let r;
      if (mode === 'session') {
        r = await api.post('/api/integrations/platform/instagram/session', { sessionid: $('#ig_sess').value.trim() });
      } else if (awaitingCode) {
        r = await api.post('/api/integrations/platform/instagram/verify', { code: $('#p_code').value.trim() });
      } else {
        r = await api.post('/api/integrations/platform/connect', { provider, username: $('#p_user').value.trim() || null, password: $('#p_pass').value || null });
      }
      const st = r.status || 'connected';
      if (st === 'connected') { closeModal(); toast(`${cap} connected`, 'good'); loadPlatforms(); return; }
      if (st === '2fa_required' || st === 'challenge_required') {
        awaitingCode = true;
        $('#p_2fa').hidden = false;
        $('#p_2fa_label').textContent = st === '2fa_required' ? 'Two-factor code (authenticator / SMS)' : 'Security code (email / SMS)';
        msg(esc(r.message || 'Enter the code to continue.'));
        btn.textContent = 'Verify code'; btn.disabled = false; $('#p_code').focus();
        return;
      }
      msg(esc(r.message || 'Could not connect.'), true);
      btn.textContent = (mode === 'password' && awaitingCode) ? 'Verify code' : 'Connect'; btn.disabled = false;
    } catch (e) { msg(esc(e.message), true); btn.textContent = 'Connect'; btn.disabled = false; }
  });
}

// ─────────────────────────────────────────────────────────
//  SETTINGS (Claude connection)
// ─────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const st = await api.get('/api/integrations/claude/status');
    const p = $('#claudePanel');
    if (st.connected) {
      p.innerHTML = `
        <div class="claude-row">
          <div class="conn-ic claude-ic big">✦</div>
          <div><div class="conn-name">Connected</div>
            <div class="muted xs">${esc(st.label || 'Claude Code (OAuth login)')} · since ${fmtDate(st.connected_at)}</div></div>
        </div>
        <div class="detail-grid">
          <div><div class="dk">Method</div><div class="dv">Claude Code OAuth login</div></div>
          <div><div class="dk">Agent SDK</div><div class="dv">v${esc(st.sdk_version || '?')}</div></div>
        </div>
        <p class="muted xs">Authenticated through the Claude Agent SDK using your Claude Code login on this machine — no API key stored.</p>
        <div class="settings-btns">
          <button class="btn btn-ghost" id="claudeRecheck">Re-check</button>
          <button class="btn btn-ghost" id="claudeDisc">Sign out</button>
        </div>`;
      $('#claudeDisc').addEventListener('click', async () => { await api.post('/api/integrations/claude/disconnect'); toast('Signed out'); loadSettings(); updateAgentBadge(); });
      $('#claudeRecheck').addEventListener('click', () => claudeLogin(true));
    } else {
      const noSdk = !st.sdk_available;
      const detected = st.cli_logged_in && !noSdk;
      p.innerHTML = `
        <div class="claude-row">
          <div class="conn-ic claude-ic big">✦</div>
          <div><div class="conn-name">${detected ? 'Claude Code detected' : 'Not connected'}</div>
            <div class="muted xs">${detected ? 'A Claude Code login was found on this machine.' : 'Sign in with your Claude Code account.'}</div></div>
        </div>
        ${noSdk
          ? `<div class="hintbox err">Claude Agent SDK is not installed. Run <code>pip install claude-agent-sdk</code> (bundled in the Docker image).</div>`
          : detected
            ? `<div class="hintbox">Detected automatically — connecting via the official Claude Agent SDK (OAuth login). No API key needed.</div>`
            : `<p class="muted xs">Uses the official <b>Claude Agent SDK</b> and your <b>Claude Code login</b> (OAuth). If you are not signed in, open Claude Code (desktop app or CLI) and log in, then retry.</p>`}
        <button class="btn btn-primary btn-block" id="claudeLogin" ${noSdk ? 'disabled' : ''}>${detected ? 'Connect Claude Code' : '✦ Login with Claude Code'}</button>
        <div id="claudeResult"></div>`;
      $('#claudeLogin')?.addEventListener('click', () => claudeLogin(false));
      // Auto-connect once if a local login is detected.
      if (detected && !sessionStorage.getItem('claudeAutoTried')) {
        try { sessionStorage.setItem('claudeAutoTried', '1'); } catch (_) {}
        claudeLogin(false);
      }
    }
  } catch (e) { toast(e.message, 'bad'); }
}

async function claudeLogin(recheck) {
  const btn = $('#claudeLogin') || $('#claudeRecheck');
  const res = $('#claudeResult');
  if (btn) { btn.disabled = true; btn.textContent = recheck ? 'Checking…' : 'Signing in via Claude Code…'; }
  if (res) res.innerHTML = '';
  try {
    const r = await api.post('/api/integrations/claude/login');
    if (r.ok) { toast('Claude Code connected ✓', 'good'); loadSettings(); updateAgentBadge(); return; }
    let msg = r.error || 'Login failed.';
    if (r.need_install) msg = 'Claude Agent SDK not installed. Run: pip install claude-agent-sdk';
    else if (r.need_login) msg = 'Claude Code is not logged in on this machine. Sign in to Claude Code (the desktop app or CLI) first, then try again.';
    if (res) res.innerHTML = `<div class="hintbox err">${esc(msg)}</div>`;
    if (btn) { btn.disabled = false; btn.textContent = recheck ? 'Re-check' : '✦ Login with Claude Code'; }
  } catch (e) {
    if (res) res.innerHTML = `<div class="hintbox err">${esc(e.message)}</div>`;
    if (btn) { btn.disabled = false; btn.textContent = recheck ? 'Re-check' : '✦ Login with Claude Code'; }
  }
}

// ── Full report download ───────────────────────────────────
document.addEventListener('click', (e) => { if (e.target.id === 'fullReportBtn') window.open('/api/report', '_blank'); });

// ── Boot ───────────────────────────────────────────────────
applyRoute();
