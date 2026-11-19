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
  $('#partner').value = config.user.partner_name || '';
  $('#provider').value = captcha.provider === 'ttshitu' ? 'ttshitu' : 'local';
  $('#tts-user').value = tts.username || '';
  $('#tts-pass').value = tts.password || '';
  $('#tts-type').value = tts.type_id || 43;
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
  $('#file-name').textContent = state.sourceName ? `当前来源：${state.sourceName}` : '';
  updatePreview();
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
  config.user.username = $('#username').value.trim();
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

// ------------------------------------------------------------------ 事件

function bind() {
  $('#config').addEventListener('input', () => {
    applyForm();
    markDirty();
    schedulePreview();
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
    markDirty();
    schedulePreview();
  });

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
  if (draft) {
    setStatus('草稿已恢复', 'dirty');
    feedback('已恢复上次未导出的编辑内容', '');
  }
}

document.addEventListener('DOMContentLoaded', boot);
