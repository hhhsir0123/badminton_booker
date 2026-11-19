/* 静态配置编辑器：载入 config.yaml → 表单编辑 → 导出 config.yaml。
 * 纯前端，无网络请求；草稿存在 localStorage，刷新不丢。 */
'use strict';

const IO = window.ConfigIO;
const DRAFT_KEY = 'bb.config.draft';
const FILE_NAME = 'config.yaml';

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
  sourceName: ''
};

const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

// ------------------------------------------------------------------ 通用

function feedback(text, kind) {
  const node = $('#feedback');
  node.textContent = text || '';
  node.className = `feedback${kind ? ' ' + kind : ''}`;
}

function setStatus(text, kind) {
  const node = $('#status');
  node.textContent = text;
  node.dataset.state = kind || '';
}

function markDirty() {
  state.dirty = true;
  setStatus('有未保存修改', 'dirty');
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
  $('#file-name').textContent = state.sourceName ? `当前来源：${state.sourceName}` : '';
  updatePreview();
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
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ config: state.config, sourceName: state.sourceName }));
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

// ------------------------------------------------------------------ 预览 / 校验

let previewTimer = null;
function updatePreview() {
  const node = $('#preview');
  try {
    node.textContent = IO.emit(state.config);
  } catch (error) {
    node.textContent = `生成失败：${error.message}`;
  }
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 200);
}

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

// ------------------------------------------------------------------ 导入 / 导出

function importFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = IO.parse(String(reader.result));
      state.config = IO.normalize(parsed);
      state.sourceName = file.name;
      state.dirty = false;
      render();
      setStatus('已载入', 'ok');
      feedback(`已载入 ${file.name}，共 ${state.config.booking.preferences.length} 条候选`, 'ok');
    } catch (error) {
      feedback(`解析失败：${error.message}`, 'error');
      setStatus('解析失败', 'error');
    }
  };
  reader.onerror = () => feedback('读取文件失败', 'error');
  reader.readAsText(file, 'utf-8');
}

function yamlText() {
  return IO.emit(state.config);
}

function download(text) {
  const blob = new Blob([text], { type: 'text/yaml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = FILE_NAME;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  }
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
  const node = $('#run-state');
  const table = {
    running: ['运行中', 'ok'],
    done: ['已完成', 'ok'],
    failed: ['失败', 'error'],
    idle: ['未运行', '']
  };
  const [text, kind] = table[status.state] || table.idle;
  node.textContent = status.state === 'running' && status.mode === 'poll' ? '轮询中' : text;
  node.dataset.state = kind;
  if (Array.isArray(status.logs) && status.logs.length) {
    const log = $('#run-log');
    log.textContent = status.logs.slice(-80).join('\n');
    log.scrollTop = log.scrollHeight;
  }
  if (status.message) {
    $('#run-hint').textContent = status.message;
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
        state.sourceName = 'config.yaml';
        render();
        setStatus('已载入', 'ok');
      }
    } catch (error) {
      /* 读不到就沿用默认值，不阻塞启停 */
    }
    setInterval(refreshStatus, 2000);
  } catch (error) {
    apiReady = false;
    $('#run-state').textContent = '静态模式';
    $('#run-hint').textContent = '直接双击打开时只能编辑配置；用 python main.py --web 启动控制台后，这里可以一键开始/停止。';
  }
}

async function saveToProgram() {
  if (!apiReady) {
    feedback('当前是静态模式，请用「导出 config.yaml」手动覆盖', 'warn');
    return;
  }
  if (!validateOrReport()) return;
  try {
    await api('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ yaml: IO.emit(state.config) })
    });
    state.dirty = false;
    setStatus('已保存', 'ok');
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
  $('#config').addEventListener('input', () => {
    applyForm();
    refreshNeeds();
    markDirty();
    schedulePreview();
  });

  $$('.tab').forEach((button) => {
    button.addEventListener('click', () => switchTab(button.dataset.tab));
  });

  $('#add').addEventListener('click', () => {
    addRow({ court: '任意', start: '19:00', end: '21:00' });
    applyForm();
    markDirty();
    schedulePreview();
  });

  $('#provider').addEventListener('change', () => {
    toggleProvider();
    applyForm();
    refreshNeeds();
    markDirty();
    schedulePreview();
  });

  $('#start').addEventListener('click', () => startRun('grab'));
  $('#start-poll').addEventListener('click', () => startRun('poll'));
  $('#stop').addEventListener('click', stopRun);
  $('#save').addEventListener('click', saveToProgram);

  $('#import').addEventListener('click', () => $('#file').click());
  $('#file').addEventListener('change', (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) importFile(file);
    event.target.value = '';
  });

  $('#export').addEventListener('click', () => {
    if (!validateOrReport()) return;
    download(yamlText());
    state.dirty = false;
    setStatus('已导出', 'ok');
    feedback(`已生成 ${FILE_NAME}，请覆盖项目根目录里的同名文件`, 'ok');
  });

  $('#copy').addEventListener('click', async () => {
    if (!validateOrReport()) return;
    const ok = await copyText(yamlText());
    feedback(ok ? '配置内容已复制到剪贴板' : '复制失败，请手动选择预览区内容', ok ? 'ok' : 'error');
  });

  $('#reset').addEventListener('click', () => {
    if (!window.confirm('恢复默认配置？当前修改会丢失。')) return;
    state.config = IO.defaults();
    state.sourceName = '';
    state.dirty = true;
    render();
    setStatus('默认配置', 'dirty');
    feedback('已恢复默认值', 'ok');
  });

  window.addEventListener('beforeunload', (event) => {
    if (!state.dirty) return undefined;
    event.preventDefault();
    event.returnValue = '';
    return '';
  });

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      $('#export').click();
    }
  });
}

function boot() {
  const draft = loadDraft();
  state.config = draft ? IO.normalize(draft.config) : IO.defaults();
  state.sourceName = draft ? draft.sourceName || '' : '';
  bind();
  render();
  detectApi();
  if (draft) {
    setStatus('草稿已恢复', 'dirty');
    feedback('已恢复上次未导出的编辑内容', '');
  }
}

document.addEventListener('DOMContentLoaded', boot);
