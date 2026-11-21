/* 静态配置编辑器：载入 config.yaml → 表单编辑 → 导出 config.yaml。
 * 纯前端，无网络请求；草稿存在 localStorage，刷新不丢。 */
'use strict';

const IO = window.ConfigIO;
const DRAFT_KEY = 'bb.config.draft';

// 场地是固定的：学院路校区羽毛球馆一共 10 片
const COURTS = [
  '任意',
  '气膜馆5号', '气膜馆6号',
  '训练馆1号', '训练馆2号', '训练馆3号', '训练馆4号',
  '训练馆7号', '训练馆8号', '训练馆9号', '训练馆10号'
];

// 平台按整点放场：候选时段只允许整点
const HOURS = Array.from({ length: 15 }, (_, index) => `${String(index + 8).padStart(2, '0')}:00`);

const state = {
  config: null,
  dirty: false,
};

const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

// ------------------------------------------------------------------ 通用

function feedback(text, kind) {
  const node = $('#feedback');
  node.textContent = text || '';
  node.className = `feedback${kind ? ' ' + kind : ''}`;
}

function markDirty() {
  state.dirty = true;
  saveDraftSoon();
}

function numberValue(selector) {
  const raw = $(selector).value.trim();
  return raw === '' ? NaN : Number(raw);
}

function pollingDay(offset) {
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date());
  const day = new Date(today + 'T00:00:00Z');
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

function showPollSelections(container, key) {
  const values = state.config.polling[key];
  const group = $(container);
  group.replaceChildren();
  values.forEach((value) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = value + ' ×';
    button.setAttribute('aria-label', '移除 ' + value);
    button.addEventListener('click', () => {
      state.config.polling[key] = values.filter((item) => item !== value);
      showPollSelections(container, key);
      markDirty();
    });
    group.appendChild(button);
  });
}

function addPollSelection(container, key, value) {
  if (!value) return;
  const polling = state.config.polling;
  polling[key] = [...new Set([...polling[key], value])].sort();
  showPollSelections(container, key);
  markDirty();
}

// ------------------------------------------------------------------ 表单

function renumber() {
  $$('.pref-row').forEach((row, index) => {
    row.querySelector('.rank').textContent = String(index + 1);
  });
}

function fillCourtOptions(select, value) {
  const options = COURTS.slice();
  // 旧配置里可能有下拉里没有的名字，保留下来，避免导出时被悄悄改掉
  if (value && options.indexOf(value) < 0) {
    options.push(value);
  }
  select.textContent = '';
  options.forEach((court) => {
    const option = document.createElement('option');
    option.value = court;
    option.textContent = court;
    select.appendChild(option);
  });
  select.value = value && options.indexOf(value) >= 0 ? value : options[0];
}

function fillHourOptions(select, value) {
  const options = HOURS.slice();
  // 旧配置里可能有非整点，保留下来，导出时提示用户改
  if (value && options.indexOf(value) < 0) {
    options.push(value);
  }
  select.textContent = '';
  options.forEach((hour) => {
    const option = document.createElement('option');
    option.value = hour;
    option.textContent = hour;
    select.appendChild(option);
  });
  select.value = value && options.indexOf(value) >= 0 ? value : options[0];
}

function addRow(item) {
  const row = $('#pref-template').content.firstElementChild.cloneNode(true);
  const courtSelect = row.querySelector('select[name="court"]');
  fillCourtOptions(courtSelect, item && item.court ? item.court : '任意');
  fillHourOptions(row.querySelector('select[name="start"]'), item && item.start ? item.start : '19:00');
  fillHourOptions(row.querySelector('select[name="end"]'), item && item.end ? item.end : '21:00');
  row.addEventListener('click', (event) => {
    const action = event.target.dataset ? event.target.dataset.act : null;
    if (!action) return;
    if (action === 'remove') {
      if ($$('.pref-row').length === 1) {
        feedback('至少保留一条候选', 'warn');
        return;
      }
      row.remove();
    } else if (action === 'up' && row.previousElementSibling) {
      row.parentNode.insertBefore(row, row.previousElementSibling);
    } else if (action === 'down' && row.nextElementSibling) {
      row.parentNode.insertBefore(row.nextElementSibling, row);
    }
    renumber();
    applyForm();
    refreshPrefSummary();
    markDirty();
  });
  $('#preferences').appendChild(row);
  renumber();
}

function renderPreferences(items) {
  $('#preferences').textContent = '';
  const list = items && items.length ? items : [{ court: '任意', start: '19:00', end: '21:00' }];
  list.forEach(addRow);
}

function render() {
  const config = state.config;
  const booking = config.booking;
  const polling = config.polling;
  const captcha = config.captcha || {};
  const tts = captcha.ttshitu || {};

  $('#release').value = booking.target_time_in_day || '07:00:00';
  $('#warmup').value = booking.advance_time;
  $('#date').value = booking.booking_date || '';
  $('#offset').value = booking.booking_days_ahead;
  $('#interval').value = polling.polling_interval;
  if (!Array.isArray(polling.polling_dates)) {
    polling.polling_dates = polling.polling_date ? [polling.polling_date]
      : [...new Set((polling.polling_date_offsets || [0]).map(pollingDay))].sort();
  }
  $('#poll-date').min = pollingDay(0);
  showPollSelections('#poll-dates', 'polling_dates');
  if (!Array.isArray(polling.polling_courts)) polling.polling_courts = [];
  showPollSelections('#poll-courts', 'polling_courts');
  const pollCourt = $('#poll-court');
  if (pollCourt) {
    pollCourt.replaceChildren(new Option('全部场地', ''));
    COURTS.filter((court) => court !== '任意').forEach((court) => {
      pollCourt.add(new Option(court, court));
    });
  }
  $('#username').value = config.user.username || '';
  $('#password').value = config.user.password || '';
  $('#partner').value = config.user.partner_name || '';
  $('#provider').value = captcha.provider === 'ttshitu' ? 'ttshitu' : 'local';
  $('#tts-user').value = tts.username || '';
  $('#tts-pass').value = tts.password || '';
  $('#tts-type').value = tts.type_id || 43;
  const [rangeStart, rangeEnd] = String(polling.polling_time_range || '19:00-21:00').split('-');
  $('#poll-start-hour').value = rangeStart;
  $('#poll-end-hour').value = rangeEnd;
  $('#poll-hours').value = String(polling.polling_hours === 2 ? 2 : 1);
  $('#auto-booking').checked = Boolean(polling.is_auto_booking);
  $('#attempts').value = booking.max_attempts;
  $('#partial').checked = Boolean(booking.allow_partial);
  $('#fallback').checked = Boolean(booking.fallback_any);
  $('#probe').value = config.runtime.release_probe_interval_ms;
  $('#webhook').value = config.feishu.webhook || '';
  $('#secret').value = config.feishu.secret || '';
  $('#mention-all').checked = Boolean(config.feishu.mention_all);
  renderPreferences(booking.preferences);
  refreshPrefSummary();
  toggleProvider();
  refreshNeeds();
}

function switchTab(name) {
  $$('.tab').forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $$('.tab-panel').forEach((panel) => {
    panel.hidden = panel.dataset.panel !== name;
  });
  // 核心日志只在抢票/轮询分区展示，系统设置不显示
  const logPanel = $('#log-panel');
  if (logPanel) logPanel.hidden = name === 'system';
  refreshNeeds();
}

function refreshNeeds() {
  const targets = [
    ['username', '#username'],
    ['password', '#password'],
    ['partner', '#partner'],
    ['tts-user', '#tts-user'],
    ['tts-pass', '#tts-pass']
  ];
  let missing = 0;
  targets.forEach(([key, selector]) => {
    const label = $(`.need[data-need="${key}"]`);
    const input = $(selector);
    if (!label || !input) return;
    const hidden = Boolean(input.closest('[hidden]'));
    const empty = !input.value.trim();
    const needs = empty && !hidden;
    label.classList.toggle('needs', needs);
    if (needs) missing += 1;
  });
  const banner = $('#banner');
  if (missing) {
    banner.hidden = false;
    banner.textContent = `还有 ${missing} 项需要你填写（标了「待填写」的字段）；其余项保持默认即可。`;
  } else {
    banner.hidden = true;
  }
}

function toggleProvider() {
  const isRemote = $('#provider').value === 'ttshitu';
  $('#tts-block').hidden = !isRemote;
}

function applyForm() {
  const config = state.config;
  config.booking.target_time_in_day = $('#release').value;
  config.booking.advance_time = numberValue('#warmup');
  config.booking.booking_date = $('#date').value;
  config.booking.booking_days_ahead = numberValue('#offset');
  config.booking.max_attempts = numberValue('#attempts');
  config.booking.allow_partial = $('#partial').checked;
  config.booking.fallback_any = $('#fallback').checked;
  config.booking.preferences = $$('.pref-row').map((row) => ({
    court: row.querySelector('select[name="court"]').value,
    start: row.querySelector('select[name="start"]').value,
    end: row.querySelector('select[name="end"]').value
  }));
  config.polling.polling_interval = numberValue('#interval');
  config.polling.polling_time_range = `${$('#poll-start-hour').value}-${$('#poll-end-hour').value}`;
  config.polling.polling_hours = Number($('#poll-hours').value) === 2 ? 2 : 1;
  config.polling.is_auto_booking = $('#auto-booking').checked;
  config.user.username = $('#username').value.trim();
  config.user.password = $('#password').value;
  config.user.partner_name = $('#partner').value.trim();
  config.captcha = config.captcha || {};
  config.captcha.provider = $('#provider').value === 'ttshitu' ? 'ttshitu' : 'local';
  const previousTts = config.captcha.ttshitu || {};
  config.captcha.ttshitu = {
    username: $('#tts-user').value.trim(),
    password: $('#tts-pass').value,
    type_id: numberValue('#tts-type') || 43,
    timeout_seconds: previousTts.timeout_seconds || 20
  };
  config.runtime.release_probe_interval_ms = numberValue('#probe');
  config.feishu.webhook = $('#webhook').value.trim();
  config.feishu.secret = $('#secret').value.trim();
  config.feishu.mention_all = $('#mention-all').checked;
}


// ------------------------------------------------------------------ 登录校验

let verifiedKey = '';

function credentialKey() {
  return `${$('#username').value.trim()}|${$('#password').value}`;
}

async function checkLogin(silent) {
  if (!apiReady) {
    feedback('页面没连上本地控制台，无法测试登录', 'warn');
    return false;
  }
  const username = $('#username').value.trim();
  const password = $('#password').value;
  if (!username) {
    markField('#username', '请填写登录手机号');
    feedback('请先填写登录手机号', 'error');
    return false;
  }
  const state = $('#login-state');
  state.textContent = '验证中…';
  state.dataset.state = 'dirty';
  if (!silent) feedback('正在用这套账号登录一次，请稍候…', '');
  try {
    const result = await api('/api/login-check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    state.textContent = '已验证';
    state.dataset.state = 'ok';
    verifiedKey = credentialKey();
    feedback(result.message || '登录验证通过', 'ok');
    return true;
  } catch (error) {
    state.textContent = '验证失败';
    state.dataset.state = 'error';
    feedback(`登录验证失败：${error.message}`, 'error');
    return false;
  }
}

function refreshPrefSummary() {
  const node = $('#pref-summary');
  if (!node) return;
  const count = $$('.pref-row').length;
  const attempts = Number($('#attempts').value) || 0;
  const tried = Math.min(count, attempts);
  node.textContent = `已配置 ${count} 条候选，最多提交 ${attempts} 次（只会尝试前 ${tried} 条）；候选条数不限。`;
}

// ------------------------------------------------------------------ 草稿

let draftTimer = null;
function saveDraftSoon() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ config: state.config }));
    } catch (error) {
      /* 隐私模式下 localStorage 可能不可用，忽略即可 */
    }
  }, 300);
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && parsed.config ? parsed : null;
  } catch (error) {
    return null;
  }
}


// ------------------------------------------------------------------ 系统设置校验

const PHONE_RE = /^1[3-9]\d{9}$/;
const NAME_RE = /^[\u4e00-\u9fa5A-Za-z·\s]{2,20}$/;

function clearFieldErrors() {
  $$('.field-error').forEach((node) => node.remove());
  $$('label.invalid').forEach((label) => label.classList.remove('invalid'));
}

function markField(selector, message) {
  const input = $(selector);
  if (!input) return;
  const label = input.closest('label');
  if (!label) return;
  label.classList.add('invalid');
  const hint = document.createElement('small');
  hint.className = 'field-error';
  hint.textContent = message;
  label.appendChild(hint);
}

function collectSystemErrors() {
  clearFieldErrors();
  const messages = [];
  const fail = (selector, message) => {
    markField(selector, message);
    messages.push(message);
  };

  const username = $('#username').value.trim();
  if (!username) {
    fail('#username', '请填写登录手机号');
  } else if (!PHONE_RE.test(username)) {
    fail('#username', '手机号应为 11 位数字');
  }

  const password = $('#password').value;
  if (!password) {
    fail('#password', '请填写登录密码');
  }

  const partner = $('#partner').value.trim();
  if (!partner) {
    fail('#partner', '请填写默认同伴');
  } else if (!NAME_RE.test(partner)) {
    fail('#partner', '同伴姓名不合法（2~20 个中文/字母）');
  }

  if ($('#provider').value === 'ttshitu') {
    if (!$('#tts-user').value.trim()) fail('#tts-user', '请填写图鉴用户名');
    if (!$('#tts-pass').value) fail('#tts-pass', '请填写图鉴密码');
    const typeId = Number($('#tts-type').value);
    if (!Number.isInteger(typeId) || typeId < 1 || typeId > 99) {
      fail('#tts-type', '题型应为 1~99 的整数');
    }
  }

  if (messages.length) {
    switchTab('system');
    feedback(messages.join('；'), 'error');
  }
  return messages;
}

// ------------------------------------------------------------------ 校验

function validateOrReport() {
  applyForm();
  const errors = IO.validate(state.config);
  if (errors.length) {
    feedback(errors.join('；'), 'error');
    return false;
  }
  feedback('');
  return true;
}

// ------------------------------------------------------------------ 本地控制台 API

let apiReady = false;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.message || response.statusText);
  }
  return data;
}

function pollingBrief(summary) {
  if (!summary || typeof summary !== "object") return "";
  const parts = [];
  if (summary.rounds) parts.push(`第 ${summary.rounds} 轮`);
  const available = summary.available || {};
  const days = Object.keys(available);
  if (days.length) {
    const total = days.reduce((sum, day) => sum + (Number(available[day]) || 0), 0);
    parts.push(`空场 ${total} 处`);
  }
  if (summary.next_scan_at) parts.push(`下次 ${String(summary.next_scan_at).slice(11, 19)}`);
  return parts.join(" · ");
}

function applyStatus(status) {
  const running = status.state === "running";
  const mode = status.mode || "";
  const grabState = $("#grab-state");
  const pollState = $("#poll-state");
  const label = (which) => {
    if (running) {
      return mode === which ? ["运行中", "ok"] : ["未运行", ""];
    }
    const table = { done: ["已完成", "ok"], failed: ["失败", "error"], stopped: ["已停止", ""], idle: ["未运行", ""] };
    return table[status.state] || table.idle;
  };
  const [gt, gk] = label("grab");
  const [pt, pk] = label("poll");
  grabState.textContent = gt;
  grabState.dataset.state = gk;
  pollState.textContent = pt;
  pollState.dataset.state = pk;
  if (running && mode === "poll") {
    $("#poll-hint").textContent = [status.message, pollingBrief(status.summary)]
      .filter(Boolean).join(" · ");
  } else if (status.message) {
    if (running && mode === "grab") $("#grab-hint").textContent = status.message;
    if (!running) {
      $("#grab-hint").textContent = status.message;
      $("#poll-hint").textContent = status.message;
    }
  }
  const core = $("#core-log");
  if (Array.isArray(status.core) && status.core.length) {
    core.textContent = status.core.slice(-40).join("\n");
    core.scrollTop = core.scrollHeight;
  }
}

async function refreshStatus() {
  if (!apiReady) return;
  try {
    applyStatus(await api('/api/status'));
  } catch (error) {
    apiReady = false;
  }
}

async function detectApi() {
  try {
    applyStatus(await api('/api/status'));
    apiReady = true;
    // 控制台模式下，以服务端的 config.yaml 为准（页面不能从空白默认值开始）
    try {
      const data = await api('/api/config');
      if (data.yaml) {
        state.config = IO.normalize(IO.parse(data.yaml));
        render();
      }
    } catch (error) {
      /* 读不到就沿用默认值，不阻塞启停 */
    }
    setInterval(refreshStatus, 2000);
  } catch (error) {
    apiReady = false;
    $('#grab-state').textContent = '静态模式';
    $('#poll-state').textContent = '静态模式';
    const hint = '页面没有连上本地控制台：请用「启动控制台」脚本打开，才能保存配置和启停程序。';
    $('#grab-hint').textContent = hint;
    $('#poll-hint').textContent = hint;
  }
}

async function saveToProgram() {
  if (!apiReady) {
    feedback('当前是静态模式，请用「导出 config.yaml」手动覆盖', 'warn');
    return;
  }
  if (collectSystemErrors().length) return;
  if (credentialKey() !== verifiedKey) {
    const ok = await checkLogin(true);
    if (!ok) return;
  }
  if (!validateOrReport()) return;
  try {
    await api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml: IO.emit(state.config) })
    });
    state.dirty = false;
    feedback('已写入 config.yaml', 'ok');
  } catch (error) {
    feedback(`保存失败：${error.message}`, 'error');
  }
}

async function startRun(mode) {
  if (!apiReady) {
    feedback('静态模式不能启动程序，请用 python main.py --web', 'warn');
    return;
  }
  if (collectSystemErrors().length) return;
  if (!validateOrReport()) return;
  try {
    // 不自动保存：改完请先点「保存配置到程序」，避免把没加载完的默认值写进去
    const result = await api('/api/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 把页面上刚校验过的配置一起提交，避免「看到的是新配置、跑的是旧配置」
      body: JSON.stringify({ mode, yaml: IO.emit(state.config) })
    });
    feedback(result.message || '已启动', 'ok');
    refreshStatus();
  } catch (error) {
    feedback(`启动失败：${error.message}`, 'error');
  }
}

async function stopRun() {
  if (!apiReady) return;
  try {
    const result = await api('/api/stop', { method: 'POST' });
    feedback(result.message || '正在停止', 'warn');
    refreshStatus();
  } catch (error) {
    feedback(`停止失败：${error.message}`, 'error');
  }
}

async function shutdownProgram() {
  if (location.protocol === 'file:') {
    feedback('当前为静态页面，没有连接本地程序。', 'warn');
    return;
  }
  if (!window.confirm('关闭程序会停止任务并关闭预约浏览器。若有待支付订单，请先完成付款。现在退出？')) return;
  try {
    await api('/api/shutdown', {
      method: 'POST', headers: { 'X-Booker-Action': 'shutdown' }
    });
    apiReady = false;
    document.querySelectorAll('button, input, select, textarea').forEach(el => { el.disabled = true; });
    const message = '已请求退出，程序会在当前操作结束后关闭。请核对已经提交的订单。';
    $('#grab-hint').textContent = message;
    $('#poll-hint').textContent = message;
    feedback(message, 'ok');
  } catch (error) {
    feedback(`关闭失败：${error.message}`, 'error');
  }
}

// ------------------------------------------------------------------ 事件

function bind() {
  $('#shutdown').addEventListener('click', shutdownProgram);
  $('#config').addEventListener('input', (event) => {
    if (event.target.id === 'username' || event.target.id === 'password') {
      verifiedKey = '';
      const state = $('#login-state');
      if (state) {
        state.textContent = '未验证';
        state.dataset.state = '';
      }
    }
    refreshPrefSummary();
    const label = event.target.closest('label');
    if (label && label.classList.contains('invalid')) {
      label.classList.remove('invalid');
      const hint = label.querySelector('.field-error');
      if (hint) hint.remove();
    }
    applyForm();
    refreshNeeds();
    markDirty();
  });

  $$('.tab').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
  });

  $('#add').addEventListener('click', () => {
    addRow({ court: '任意', start: '19:00', end: '21:00' });
    applyForm();
    markDirty();
  });

  $('#provider').addEventListener('change', () => {
    toggleProvider();
    applyForm();
    refreshNeeds();
    markDirty();
  });

  $('#start').addEventListener('click', () => startRun('grab'));
  $('#stop').addEventListener('click', stopRun);
  ['#poll-start-hour', '#poll-end-hour'].forEach((selector) => {
    const select = $(selector);
    HOURS.forEach((hour) => select.add(new Option(hour, hour)));
    select.add(new Option('23:00', '23:00'));
  });
  $('#poll-start-hour').value = '19:00';
  $('#poll-end-hour').value = '21:00';
  ['#poll-start-hour', '#poll-end-hour', '#poll-hours'].forEach((selector) => {
    $(selector).addEventListener('change', () => { applyForm(); markDirty(); });
  });
  $('#add-poll-date').addEventListener('click', () => {
    const picker = $('#poll-date');
    if (picker.reportValidity()) addPollSelection('#poll-dates', 'polling_dates', picker.value);
  });

  $('#add-poll-court').addEventListener('click', () => {
    const select = $('#poll-court');
    const value = select.value || '全部场地';
    if (value === '全部场地') {
      // 「全部场地」是互斥项：选它就清掉具体场地
      state.config.polling.polling_courts = ['全部场地'];
    } else {
      const rest = (state.config.polling.polling_courts || []).filter((item) => item !== '全部场地');
      state.config.polling.polling_courts = [...new Set([...rest, value])].sort();
    }
    showPollSelections('#poll-courts', 'polling_courts');
    markDirty();
    select.value = '';
  });
  $('#poll-start').addEventListener('click', () => startRun('poll'));
  $('#poll-stop').addEventListener('click', stopRun);
  $('#save').addEventListener('click', saveToProgram);
  $('#test-login').addEventListener('click', () => checkLogin(false));

  window.addEventListener('beforeunload', (event) => {
    if (!state.dirty) return undefined;
    event.preventDefault();
    event.returnValue = '';
    return '';
  });

}

function boot() {
  const draft = loadDraft();
  state.config = draft ? IO.normalize(draft.config) : IO.defaults();
  bind();
  render();
  detectApi();
  if (draft) {
    feedback('已恢复上次未保存的编辑内容', '');
  }
}

document.addEventListener('DOMContentLoaded', boot);
