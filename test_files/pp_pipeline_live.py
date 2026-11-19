"""乒乓球真机联调：Selenium 走完整链路（预解 token → 选场 → 代提交）。

流程：
  1. 打开乒乓球预约页，切到 2026-11-21
  2. 选一个空场 + 选第一个同伴
  3. 点提交 → 验证码弹出
  4. SubmitCaptchaFlow.pre_solve()：图鉴平台识别（本地做一致性校验）→ 点击 → 拿 token
  5. 用 token + 真实表单调 submitData → 等「提交预约成功」
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from src.config import Config  # noqa: E402
from src.core.browser import BrowserManager  # noqa: E402
from src.core.submit_captcha import SubmitCaptchaFlow  # noqa: E402
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
  t.search.date = date;
  t.getReserveDate();
  return 'loading';
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

SELECTED_JS = """
return (function () {
  const seen = new Set(); let t = null;
  function walk(vm, d) { if (!vm || d > 20 || seen.has(vm)) return; seen.add(vm);
    if (vm.$data && vm.$data.tableVenueSite) t = vm;
    for (const c of (vm.$children || [])) walk(c, d + 1); }
  for (const el of document.querySelectorAll('*')) { const vm = el.__vue__; if (!vm) continue; walk(vm, 0); }
  if (!t) return null;
  return {selected: (t.$data.selected || []).length, date: t.$data.search.date};
})();
"""


def main():
    config = Config(os.path.join(ROOT, "config.yaml"))
    provider = None
    if str(config.get("captcha.provider", "local")).lower() == "ttshitu":
        provider = TTShituProvider.from_config(config)
        print("使用图鉴:", provider.username, "typeid", provider.type_id)

    platform_solver = ClickWordSolver(provider=provider) if provider else ClickWordSolver()
    local_solver = ClickWordSolver()
    local_solver.warmup()

    profile = os.path.join(ROOT, ".chrome-profile")
    browser = BrowserManager(headless=True, profile_dir=profile, project_root=ROOT)
    driver = browser.start()
    try:
        driver.get(URL)
        time.sleep(8)
        print("url:", driver.current_url)
        print("tables:", driver.execute_script("return document.querySelectorAll('table').length;"))
        print("set date:", driver.execute_script(SET_DATE_JS, DATE))
        time.sleep(4)
        pick = driver.execute_script(PICK_JS)
        print("pick:", pick)
        time.sleep(0.8)
        print("buddy:", driver.execute_script(BUDDY_JS))
        time.sleep(0.8)
        print("selected:", driver.execute_script(SELECTED_JS))

        flow = SubmitCaptchaFlow(driver, platform_solver, local_solver)
        # 必须在验证码组件创建前劫持 verifySuccess
        print("patch:", flow.patch())

        # 点提交，唤起验证码（submitForm 同时被填充）
        button = driver.find_element(By.CSS_SELECTOR, ".submit_order_box .action .btn:not(.cancel)")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
        button.click()
        time.sleep(2.5)
        info = flow.read()
        print("验证码:", "有" if info else "无", (info or {}).get("words"))

        started = time.monotonic()
        captured = flow.pre_solve()
        cost = int((time.monotonic() - started) * 1000)
        if not captured:
            print("预解失败，结束")
            return
        print(f"预解成功 {cost}ms，token={captured.token[:8]}，表单字段={list(captured.form.keys())}")

        time.sleep(1)
        if not flow.submit_selected():
            print("代提交失败")
            return
        ok = flow.wait_success(timeout=8)
        print("提交结果:", "成功" if ok else "失败/未知")
        print("页面尾部:", driver.execute_script(
            "return (document.body && document.body.innerText || '').slice(-300);"))
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
