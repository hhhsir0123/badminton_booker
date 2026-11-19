"""诊断：对比平台坐标与本地坐标，并看点击后的验证码提示。"""
import base64
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selenium.webdriver.common.by import By  # noqa: E402

from src.config import Config  # noqa: E402
from src.core.browser import BrowserManager  # noqa: E402
from src.utils.click_word_solver import ClickWordSolver  # noqa: E402
from src.utils.ttshitu_provider import TTShituProvider  # noqa: E402

URL = "https://ggtypt.cumtb.edu.cn/venue/venue-reservation/1"
DATE = "2026-11-21"

PICK_JS = """
return (function () {
  const tables = [...document.querySelectorAll('table')].filter(t => t.getClientRects().length);
  for (const table of tables) {
    const rows = [...table.querySelectorAll('tr')];
    const head = rows.find(r => r.children.length > 2);
    if (!head) continue;
    const header = [...head.children].map(c => (c.innerText || '').trim());
    for (const row of rows) {
      if (row === head) continue;
      const cells = [...row.children];
      for (let i = 1; i < cells.length; i++) {
        const block = cells[i].querySelector('.reserveBlock.free');
        if (!block) continue;
        ['mousedown', 'mouseup', 'click'].forEach(t => block.dispatchEvent(new MouseEvent(t, {bubbles: true})));
        return {ok: true, time: (cells[0].innerText || '').trim(), court: header[i]};
      }
    }
  }
  return {ok: false};
})();
"""

SET_DATE_JS = """
return (function (date) {
  const seen = new Set(); let t = null;
  function walk(vm, d) { if (!vm || d > 20 || seen.has(vm)) return; seen.add(vm);
    if (vm.$data && vm.$data.tableVenueSite) t = vm;
    for (const c of (vm.$children || [])) walk(c, d + 1); }
  for (const el of document.querySelectorAll('*')) { const vm = el.__vue__; if (!vm) continue; walk(vm, 0); }
  if (!t) return 'no-vm';
  t.search.date = date; t.getReserveDate(); return 'loading';
})(arguments[0]);
"""

BUDDY_JS = """
return (function () {
  const row = document.querySelector('.companion_box .buddy-row');
  if (!row) return 'no-buddy';
  ['mousedown', 'mouseup', 'click'].forEach(t => row.dispatchEvent(new MouseEvent(t, {bubbles: true})));
  return (row.innerText || '').trim();
})();
"""

READ_JS = """
return (function () {
  function walk(vm, depth) {
    if (!vm || depth > 18) { return null; }
    if (vm.$data && vm.$data.pointBackImgBase !== undefined) { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) {
    const vm = el.__vue__; if (!vm) continue;
    const hit = walk(vm, 0);
    if (hit) { return {image: hit.$data.pointBackImgBase, words: hit.$data.poinTextList,
                       setSize: hit.$data.setSize, text: hit.$data.text || ''}; }
  }
  return null;
})();
"""

CLICK_JS = """
return (function (points) {
  function walk(vm, depth) {
    if (!vm || depth > 18) { return null; }
    if (vm.$data && vm.$data.pointBackImgBase !== undefined) { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  let vm = null;
  for (const el of document.querySelectorAll('*')) { const v = el.__vue__; if (!v) continue; const h = walk(v, 0); if (h) { vm = h; break; } }
  if (!vm) return 'no-vm';
  const s = vm.$data.setSize || {};
  const w = parseInt(s.imgWidth) || 310, h = parseInt(s.imgHeight) || 155;
  for (const p of points) { vm.canvasClick({offsetX: p[0] * w / 310, offsetY: p[1] * h / 155}); }
  return 'clicked';
})(arguments[0]);
"""

STATE_JS = """
return (function () {
  function walk(vm, depth) {
    if (!vm || depth > 18) { return null; }
    if (vm.$data && vm.$data.pointBackImgBase !== undefined) { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) { const vm = el.__vue__; if (!vm) continue; const h = walk(vm, 0); if (h) return {text: h.$data.text || ''}; }
  return null;
})();
"""

PATCH_JS = """
return (function () {
  function walk(vm, depth) {
    if (!vm || depth > 20) { return null; }
    if (typeof vm.submitData === 'function' && typeof vm.verifySuccess === 'function') { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) { const vm = el.__vue__; if (!vm) continue; const h = walk(vm, 0); if (h) {
    window.__bbVerify = null;
    h.verifySuccess = function (v, t) { window.__bbVerify = {verification: v, token: t, form: JSON.parse(JSON.stringify(h.submitForm || {}))}; };
    return 'patched';
  } }
  return 'no-form';
})();
"""


def main():
    config = Config(os.path.join(ROOT, "config.yaml"))
    provider = TTShituProvider.from_config(config)
    platform = ClickWordSolver(provider=provider)
    local = ClickWordSolver()
    local.warmup()

    browser = BrowserManager(headless=True, profile_dir=os.path.join(ROOT, ".chrome-profile"), project_root=ROOT)
    driver = browser.start()
    try:
        driver.get(URL)
        time.sleep(8)
        print("date:", driver.execute_script(SET_DATE_JS, DATE))
        time.sleep(4)
        print("pick:", driver.execute_script(PICK_JS))
        time.sleep(0.8)
        print("buddy:", driver.execute_script(BUDDY_JS))
        time.sleep(0.8)
        print("patch:", driver.execute_script(PATCH_JS))

        button = driver.find_element(By.CSS_SELECTOR, ".submit_order_box .action .btn:not(.cancel)")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        button.click()
        time.sleep(2.5)

        info = driver.execute_script(READ_JS)
        if not info:
            print("没有验证码")
            return
        print("words:", info["words"], "setSize:", info.get("setSize"))
        png = base64.b64decode(info["image"].split(",", 1)[-1])

        started = time.monotonic()
        p = platform.solve(png, info["words"])
        print("平台:", p, f"{int((time.monotonic()-started)*1000)}ms")
        started = time.monotonic()
        l = local.solve(png, info["words"])
        print("本地:", l, f"{int((time.monotonic()-started)*1000)}ms")
        if p and l:
            print("差异:", [abs(a[0]-b[0]) + abs(a[1]-b[1]) for a, b in zip(p, l)])

        target = p or l
        if not target:
            print("两边都没结果")
            return
        print("点击:", driver.execute_script(CLICK_JS, [list(x) for x in target]))
        time.sleep(3)
        print("state:", driver.execute_script(STATE_JS))
        print("captured:", driver.execute_script("return window.__bbVerify || null;"))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
