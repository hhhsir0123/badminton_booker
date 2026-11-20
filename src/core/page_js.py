"""注入到场馆页面的 JS 工具箱。

为什么要把逻辑放进浏览器里：
Selenium 的每个 execute_script 都是一次 WebSocket 往返（本机 5~20ms，加上轮询等待通常
要 100ms 级）。原来的代码用「Python 循环 + time.sleep」等待页面，一次选场要好几个往返
和好几秒固定 sleep。这里改成：
  1. 用 CDP 在每次导航前注入 window.__bb（幂等）；
  2. 所有「等待某个状态出现」都在页面内用 setTimeout/rAF 轮询完成；
  3. Python 只发一次 execute_async_script，拿回一个纯 JSON 结果。

注入放在 document 开始执行之前，所以刷新、跳转后都还在，不需要重复注入。
"""

# 注入脚本：必须幂等（重载后重新执行），且不能依赖外部库。
HELPERS = r"""
(function () {
  if (window.__bb) { return; }

  var bb = {};

  // 保存场次请求的脱敏结果，不能把资源加载完成误当业务成功。
  bb.dayResponses = [];
  bb.recordDayResponse = function (url, body, status, raw, since) {
    try {
      var parsed = new URL(url, location.origin);
      if (!parsed.pathname.endsWith('/api/reservation/day/info')) { return; }
      var day = parsed.searchParams.get('searchDate');
      if (!day && body) {
        if (typeof body === 'string') {
          try { day = JSON.parse(body).searchDate; } catch (_) { day = new URLSearchParams(body).get('searchDate'); }
        } else if (body.get) { day = body.get('searchDate'); }
      }
      var payload;
      try { payload = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (_) { payload = null; }
      var code = payload && payload.code;
      var dates = payload && payload.data && payload.data.reservationDateSpaceInfo;
      var rows = dates && dates[day];
      var valid = Array.isArray(rows) && rows.every(function (row) {
        return row && typeof row === 'object' && !Array.isArray(row) && Object.keys(row).every(function (key) {
          if (['venueSpaceGroupId', 'spaceName', 'enSpaceName', 'id', 'venueSiteId'].indexOf(key) >= 0) { return true; }
          var cell = row[key];
          if (!cell || typeof cell !== 'object' || [1, 2, 3, 4].indexOf(cell.reservationStatus) < 0) { return false; }
          return cell.reservationStatus !== 1 || (typeof cell.spaceName === 'string' && cell.spaceName.trim()
            && typeof cell.startDate === 'string' && cell.startDate.indexOf(day + ' ') === 0
            && typeof cell.endDate === 'string' && cell.endDate.indexOf(day + ' ') === 0);
        });
      });
      var ok = status === 200 && (code === 200 || code === '200') && valid;
      bb.dayResponses.push({ date: day, since: since, ok: !!ok, status: status,
        error: status !== 200 ? 'http-error' : (!payload ? 'invalid-json' :
          ((code !== 200 && code !== '200') ? 'business-error' : (!valid ? 'invalid-date-data' : null))) });
      if (bb.dayResponses.length > 30) { bb.dayResponses.shift(); }
    } catch (_) { /* 诊断不得干扰页面原有请求 */ }
  };
  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__bbUrl = url;
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    var xhr = this, since = performance.now(), url = xhr.__bbUrl;
    xhr.addEventListener('loadend', function () {
      try { bb.recordDayResponse(url, body, xhr.status, xhr.responseType === 'json' ? xhr.response : xhr.responseText, since); }
      catch (_) { /* 不读取非文本响应 */ }
    }, { once: true });
    return originalSend.apply(this, arguments);
  };
  if (window.fetch) {
    var originalFetch = window.fetch;
    window.fetch = function (input, options) {
      var since = performance.now();
      var url = typeof input === 'string' ? input : input.url;
      var body = options && options.body;
      return originalFetch.apply(this, arguments).then(function (response) {
        if (String(url).indexOf('/api/reservation/day/info') >= 0) {
          response.clone().text().then(function (raw) { bb.recordDayResponse(url, body, response.status, raw, since); }).catch(function () {});
        }
        return response;
      });
    };
  }

  bb.visible = function (el) {
    if (!el || !el.getClientRects || !el.getClientRects().length) { return false; }
    var style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };

  bb.text = function (el) {
    if (!el) { return ''; }
    return (el.innerText || el.textContent || '').replace(/\s+/g, '');
  };

  bb.sleep = function (ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  };

  // 页面内轮询，避免 Python 侧一次次 execute_script。
  bb.waitFor = function (fn, timeout, interval) {
    timeout = timeout == null ? 3000 : timeout;
    interval = interval || 25;
    var start = performance.now();
    return new Promise(function (resolve) {
      function tick() {
        var value = null;
        try { value = fn(); } catch (error) { value = null; }
        if (value) { resolve(value); return; }
        if (performance.now() - start >= timeout) { resolve(null); return; }
        setTimeout(tick, interval);
      }
      tick();
    });
  };

  bb.courtName = function (value) {
    return String(value || '').replace(/[→›»]/g, '');
  };

  bb.table = function () {
    var tables = [].slice.call(document.querySelectorAll('.reserveTable .spaceTable table'))
      .filter(bb.visible);
    return tables.length === 1 ? tables[0] : null;
  };

  bb.headerLabels = function (table) {
    var head = table.tHead && table.tHead.rows[0];
    if (!head) { return null; }
    return [].slice.call(head.cells).map(function (cell) { return bb.text(cell); });
  };

  bb.arrows = function (table) {
    var head = table.tHead && table.tHead.rows[0];
    if (!head) { return { prev: null, next: null }; }
    // iview 的分页箭头即使禁用也会渲染，必须看 disabled / pointer-events，
    // 否则会误判「还有上一页」，在抢场热路径上白等一个超时。
    var pick = function (direction) {
      var items = [].slice.call(head.querySelectorAll('.ivu-icon-ios-arrow-' + direction))
        .filter(bb.visible);
      var el = items[0];
      if (!el) { return null; }
      var parent = el.parentElement;
      var disabled = /\bdisabled\b/.test(String(el.className || ''))
        || (parent && /\bdisabled\b/.test(String(parent.className || '')))
        || getComputedStyle(el).pointerEvents === 'none'
        || (parent && getComputedStyle(parent).pointerEvents === 'none');
      return disabled ? null : el;
    };
    return { prev: pick('back'), next: pick('forward') };
  };

  // 返回纯 JSON：表头、每行标签、每个格子的 class。element 引用不出页面。
  bb.snapshot = function () {
    var table = bb.table();
    if (!table) { return { error: 'no-table' }; }
    var labels = bb.headerLabels(table);
    if (!labels) { return { error: 'no-header' }; }
    var body = table.tBodies && table.tBodies[0];
    if (!body) { return { error: 'no-body' }; }
    var rows = [].slice.call(body.rows).map(function (row) {
      var cells = [].slice.call(row.cells);
      return {
        label: bb.text(cells[0]),
        cells: cells.slice(1).map(function (cell) {
          var block = cell.querySelector('.reserveBlock');
          return block ? { classes: Array.prototype.slice.call(block.classList) } : null;
        })
      };
    });
    var arrows = bb.arrows(table);
    return {
      labels: labels,
      reversed: labels[0] === '场地',
      rows: rows,
      prev: !!arrows.prev,
      next: !!arrows.next
    };
  };

  // 同时支持「场地为列」和「场地为行」两种表头布局。
  bb.cellFor = function (court, slot) {
    var table = bb.table();
    if (!table) { return { error: 'no-table' }; }
    var labels = bb.headerLabels(table);
    if (!labels) { return { error: 'no-header' }; }
    var body = table.tBodies && table.tBodies[0];
    if (!body) { return { error: 'no-body' }; }
    var rows = [].slice.call(body.rows);
    var wantedCourt = bb.courtName(court);
    var index;

    if (labels[0] === '场地') {
      for (var i = 0; i < rows.length; i += 1) {
        if (bb.courtName(bb.text(rows[i].cells[0])) !== wantedCourt) { continue; }
        index = labels.indexOf(slot);
        if (index < 1 || index >= rows[i].cells.length) { return { error: 'slot-not-found' }; }
        return { cell: rows[i].cells[index] };
      }
      return { error: 'court-not-found' };
    }

    index = labels.indexOf(court);
    if (index < 1) { return { error: 'court-not-found' }; }
    for (var j = 0; j < rows.length; j += 1) {
      if (bb.text(rows[j].cells[0]) !== slot) { continue; }
      if (index >= rows[j].cells.length) { return { error: 'slot-not-found' }; }
      return { cell: rows[j].cells[index] };
    }
    return { error: 'slot-not-found' };
  };

  // 用真实鼠标事件点击场格：Vue 的 @click 监听在元素或父级上，冒泡即可触发。
  bb.selectCell = function (court, slot) {
    var found = bb.cellFor(court, slot);
    if (found.error) { return { ok: false, error: found.error }; }
    var block = found.cell.querySelector('.reserveBlock');
    if (!block) { return { ok: false, error: 'no-block' }; }
    var before = block.className;
    var selectedBefore = document.querySelectorAll('.reserveBlock.selected, .reserveBlock.active').length;
    var rect = block.getBoundingClientRect();
    var options = {
      bubbles: true, cancelable: true, view: window, button: 0,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2
    };
    block.dispatchEvent(new MouseEvent('mousedown', options));
    block.dispatchEvent(new MouseEvent('mouseup', options));
    block.dispatchEvent(new MouseEvent('click', options));

    // 重新查一次：Vue 可能把整个格子重渲染，旧引用会失效
    var again = bb.cellFor(court, slot);
    var current = (!again.error && again.cell.querySelector('.reserveBlock')) || block;
    var after = String(current.className || '');
    var selectedAfter = document.querySelectorAll('.reserveBlock.selected, .reserveBlock.active').length;
    return {
      ok: true,
      before: String(before),
      after: after,
      changed: String(before) !== after || selectedAfter > selectedBefore,
      classes: after.split(/\s+/).filter(Boolean)
    };
  };

  bb.toasts = function () {
    var selectors = ['.ivu-message', '.ivu-notice', '.ant-message', '.el-message', '.toast'];
    var texts = [];
    selectors.forEach(function (selector) {
      [].slice.call(document.querySelectorAll(selector)).forEach(function (node) {
        if (!bb.visible(node)) { return; }
        var value = bb.text(node);
        if (value) { texts.push(value); }
      });
    });
    return texts;
  };

  // 提交并等待平台回执。返回 ok/reason/message，不做任何重试。
  bb.submit = async function (timeout) {
    var scope = document.querySelector('.submit_order_box.pc') || document.querySelector('.submit_order_box');
    if (!scope) { return { ok: false, reason: 'no-submit-box' }; }
    var button = scope.querySelector('.btn') || scope.querySelector('button');
    if (!button) { return { ok: false, reason: 'no-submit-button' }; }
    if (String(button.className || '').indexOf('disabled') >= 0) {
      return { ok: false, reason: 'submit-disabled', message: bb.text(scope).slice(0, 120) };
    }

    bb._href = location.href;
    bb._baseline = bb.toasts().join('|');
    var rect = button.getBoundingClientRect();
    var options = {
      bubbles: true, cancelable: true, view: window, button: 0,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2
    };
    button.dispatchEvent(new MouseEvent('mousedown', options));
    button.dispatchEvent(new MouseEvent('mouseup', options));
    button.dispatchEvent(new MouseEvent('click', options));
    return bb.awaitOutcome(timeout);
  };

  // 只观察结果，不再点击提交按钮（验证码处理完之后用）。
  bb.awaitOutcome = async function (timeout, baseline) {
    var href = bb._href || location.href;
    baseline = baseline === undefined ? (bb._baseline || '') : baseline;
    var started = performance.now();
    var confirmed = false;
    while (performance.now() - started < (timeout || 6000)) {
      if (location.href !== href) {
        return { ok: true, reason: 'navigated', url: location.href };
      }
      // 出现点选验证码：交给 Python 侧的 ddddocr 处理，处理完再回来观察
      var captcha = [].slice.call(document.querySelectorAll('.verify-img-panel, div[class*="verify"]'))
        .filter(bb.visible)[0];
      if (captcha) {
        return { ok: false, reason: 'captcha', message: bb.text(captcha).slice(0, 80) };
      }
      // 可能弹出二次确认框
      var dialogs = [].slice.call(document.querySelectorAll('.ivu-modal-wrap,.ivu-modal,.el-dialog,.ant-modal'))
        .filter(bb.visible);
      for (var i = 0; i < dialogs.length; i += 1) {
        var buttons = [].slice.call(dialogs[i].querySelectorAll('button,.ivu-btn'));
        for (var j = 0; j < buttons.length; j += 1) {
          if (!bb.visible(buttons[j])) { continue; }
          var label = bb.text(buttons[j]);
          if (label === '确定' || label === '确认' || label === '确认预约' || label === '提交') {
            if (!confirmed) {
              buttons[j].click();
              confirmed = true;
            }
          }
        }
      }
      var current = bb.toasts().join('|');
      var fresh = current && current !== baseline ? current : '';
      // 先判失败：「下单失败」也含「下单」，不能先被成功规则吃掉
      if (/失败|已被|已售|库存不足|不可预约|频繁|超时|已过期|请勿重复/.test(fresh)) {
        return { ok: false, reason: 'toast', message: fresh };
      }
      if (/成功|已生成|待支付|去支付|订单号/.test(fresh)) {
        return { ok: true, reason: 'toast', message: fresh };
      }
      await bb.sleep(40);
    }
    return { ok: false, reason: 'timeout', message: bb.toasts().join('|') };
  };

  // 直接请求页面自己用的 day/info 接口，用来判断「放场了没有」。
  // 只是加速信号，失败不影响主流程。
  bb.probe = async function (date) {
    var started = performance.now();
    try {
      var url = location.origin + '/api/reservation/day/info?searchDate=' + encodeURIComponent(date)
        + '&_=' + Date.now();
      var response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
      var body = await response.text();
      return {
        status: response.status,
        length: body.length,
        ms: performance.now() - started,
        sample: body.slice(0, 160)
      };
    } catch (error) {
      return { status: 0, length: 0, error: String(error), ms: performance.now() - started };
    }
  };

  window.__bb = bb;
})();
"""


# 等待页面内某个条件成立；条件用 JS 表达式，返回真值即完成。
WAIT_JS = """
const done = arguments[arguments.length - 1];
const [condition, timeout] = arguments;
if (!window.__bb) { done(false); return; }
window.__bb.waitFor(function () { return eval(condition); }, timeout || 3000)
  .then(function (value) { done(value === null ? false : value); })
  .catch(function () { done(false); });
"""


# 日期条的点击：返回点击前的 performance.now() 便于匹配这次请求。
CLICK_DATE_JS = """
const done = arguments[arguments.length - 1];
const [index, timeout, wanted] = arguments;
(function () {
  const tabs = [...document.querySelectorAll('.date_box > div')].filter(e => e.getClientRects().length);
  const tab = tabs[index];
  if (!tab) { done({ ok: false, error: 'no-tab', count: tabs.length }); return; }
  const since = performance.now();
  tab.click();
  const started = performance.now();
  (function wait() {
    const hit = window.__bb && window.__bb.dayResponses.find(function (entry) {
      return entry.since >= since && entry.date === wanted;
    });
    if (hit) { done({ ok: hit.ok, error: hit.error, status: hit.status, since: since, ms: performance.now() - started }); return; }
    if (performance.now() - started > (timeout || 900)) {
      done({ ok: false, error: 'no-response', since: since, ms: performance.now() - started });
      return;
    }
    setTimeout(wait, 20);
  })();
})();
"""


# 等两帧渲染，确保 Vue 把新数据刷到 DOM 上。
FRAMES_JS = """
const done = arguments[arguments.length - 1];
requestAnimationFrame(function () {
  requestAnimationFrame(function () { done(true); });
});
"""


# 一次调用完成「选中若干场格」，返回每格的实际结果。
SELECT_CELLS_JS = """
const done = arguments[arguments.length - 1];
const [targets] = arguments;
if (!window.__bb) { done({ ok: false, error: 'no-helpers' }); return; }
(function () {
  const results = [];
  (async function () {
    for (const target of targets) {
      const result = window.__bb.selectCell(target.court, target.slot);
      results.push(Object.assign({ court: target.court, slot: target.slot }, result));
      await window.__bb.sleep(60);
    }
    done({ ok: true, results: results });
  })().catch(function (error) { done({ ok: false, error: String(error), results: results }); });
})();
"""


SUBMIT_JS = """
const done = arguments[arguments.length - 1];
const [timeout] = arguments;
if (!window.__bb) { done({ ok: false, reason: 'no-helpers' }); return; }
window.__bb.submit(timeout).then(done).catch(function (error) {
  done({ ok: false, reason: 'error', message: String(error) });
});
"""


# 只观察结果，不重复点击提交（验证码处理完之后调用）。
AWAIT_OUTCOME_JS = """
const done = arguments[arguments.length - 1];
const [timeout, baseline] = arguments;
if (!window.__bb) { done({ ok: false, reason: 'no-helpers' }); return; }
window.__bb.awaitOutcome(timeout, baseline).then(done).catch(function (error) {
  done({ ok: false, reason: 'error', message: String(error) });
});
"""


PROBE_JS = """
const done = arguments[arguments.length - 1];
const [date] = arguments;
if (!window.__bb) { done({ status: 0, length: 0, error: 'no-helpers' }); return; }
window.__bb.probe(date).then(done).catch(function (error) {
  done({ status: 0, length: 0, error: String(error) });
});
"""


SNAPSHOT_JS = """
const done = arguments[arguments.length - 1];
if (!window.__bb) { done({ error: 'no-helpers' }); return; }
done(window.__bb.snapshot());
"""


# 用同源 HEAD 请求读服务器的 Date 头，用来估算本机时钟偏差。
SERVER_TIME_JS = """
const done = arguments[arguments.length - 1];
if (!window.__bb) { done({ error: 'no-helpers' }); return; }
fetch(location.origin + '/', { method: 'HEAD', cache: 'no-store', credentials: 'same-origin' })
  .then(function (response) { done({ date: response.headers.get('date') }); })
  .catch(function (error) { done({ error: String(error) }); });
"""
