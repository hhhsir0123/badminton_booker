/* 静态配置编辑器：载入 config.yaml → 表单编辑 → 导出 config.yaml。
 * 纯前端，无网络请求；草稿存在 localStorage，刷新不丢。 */
'use strict';

const IO = window.ConfigIO;
const DRAFT_KEY = 'bb.config.draft';

// 场地是固定的：学院路校区羽毛球馆一共 10 片
const COURTS = [
  '任意',
  '训练馆1号', '训练馆2号', '训练馆3号', '训练馆4号',
  '气膜馆5号', '气膜馆6号',
  '训练馆7号', '训练馆8号', '训练馆9号', '训练馆10号'
];

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

function parseDays(text) {
  return String(text)
    .replace(/，/g, ',')
    .split(',')
    .map((token) => token.trim())
    .filter((token) => token.length)
    .map((token) => (/^-?\d+$/.test(token) ? Number(token) : NaN));
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

function addRow(item) {
  const row = $('#pref-template').content.firstElementChild.cloneNode(true);
  const courtSelect = row.querySelector('select[name="court"]');
  fillCourtOptions(courtSelect, item && item.court ? item.court : '任意');
  row.querySelector('input[name="start"]').value = item && item.start ? item.start : '19:00';
  row.querySelector('input[name="end"]').value = item && item.end ? item.end : '21:00';
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
  $('#days').value = (polling.polling_date_offsets || []).join(', ');
  $('#username').value = config.user.username || '';
  $('#password').value = config.user.password || '';
  $('#partner').value = config.user.partner_name || '';
  $('#provider').value = captcha.provider === 'ttshitu' ? 'ttshitu' : 'local';
  $('#tts-user').value = tts.username || '';
  $('#tts-pass').value = tts.password || '';
  $('#tts-type').value = tts.type_id || 43;
  $('#poll-slots').value = (polling.polling_time_slots || []).join(', ');
  $('#auto-booking').checked = Boolean(polling.is_auto_booking);
  $('#attempts').value = booking.max_attempts;
  $('#partial').checked = Boolean(booking.allow_partial);
  $('#fallback').checked = Boolean(booking.fallback_any);
  $('#probe').value = config.runtime.release_probe_interval_ms;
  $('#keep').value = config.runtime.keep_open_after_success;
  $('#webhook').value = config.feishu.webhook || '';
  $('#secret').value = config.feishu.secret || '';
  $('#mention-all').checked = Boolean(config.feishu.mention_all);
  renderPreferences(booking.preferences);
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
  refreshNeeds();
}

function refreshNeeds() {
  const targets = [
    ['username', '#username'],
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
    start: row.querySelector('input[name="start"]').value,
    end: row.querySelector('input[name="end"]').value
  }));
  config.polling.polling_interval = numberValue('#interval');
  config.polling.polling_date_offsets = parseDays($('#days').value);
  config.polling.polling_time_slots = $('#poll-slots').value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length);
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
  config.runtime.keep_open_after_success = numberValue('#keep');
  config.feishu.webhook = $('#webhook').value.trim();
  config.feishu.secret = $('#secret').value.trim();
  config.feishu.mention_all = $('#mention-all').checked;
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

  const partner = $('#partner').value.trim();
  if (partner && !NAME_RE.test(partner)) {
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
  if (status.message) {
    if (running && mode === "grab") $("#grab-hint").textContent = status.message;
    if (running && mode === "poll") $("#poll-hint").textContent = status.message;
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
      body: JSON.stringify({ mode })
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

// ------------------------------------------------------------------ 事件

function bind() {
  $('#config').addEventListener('input', (event) => {
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
  $('#poll-start').addEventListener('click', () => startRun('poll'));
  $('#poll-stop').addEventListener('click', stopRun);
  $('#save').addEventListener('click', saveToProgram);

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
