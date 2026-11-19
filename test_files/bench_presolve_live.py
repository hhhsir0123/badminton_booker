"""预解通过率统计：连续 10 张验证码，只做预解（拿 token），绝不提交。

每张：
  1. 读图 + 目标词
  2. 图鉴 typeid=43 识别坐标（计时）
  3. 点击 → 看 verifySuccess 是否被调用（拿到 token 即算通过）
  4. 刷新验证码，进入下一张
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from selenium.webdriver.common.by import By  # noqa: E402

from src.config import Config  # noqa: E402
from src.core.browser import BrowserManager  # noqa: E402
from src.core.submit_captcha import SubmitCaptchaFlow  # noqa: E402
from src.utils.click_word_solver import ClickWordSolver  # noqa: E402
from src.utils.ttshitu_provider import TTShituProvider  # noqa: E402

URL = "https://ggtypt.cumtb.edu.cn/venue/venue-reservation/1"
DATE = "2026-11-21"
ROUNDS = int(os.environ.get("ROUNDS", "10"))

SET_DATE_JS = """
return (function (date) {
  const seen = new Set(); let t = null;
  function walk(vm, d) { if (!vm || d > 20 || seen.has(vm)) return; seen.add(vm);
    if (vm.$data && vm.$data.tableVenueSite) t = vm;
    for (const c of (vm.$children || [])) walk(c, d + 1); }
  for (const el of document.querySelectorAll('*')) { const vm = el.__vue__; if (!vm) continue; walk(vm, 0); }
  if (!t) return 'no-vm'; t.search.date = date; t.getReserveDate(); return 'loading';
})(arguments[0]);
"""

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
        ['mousedown','mouseup','click'].forEach(t => block.dispatchEvent(new MouseEvent(t, {bubbles: true})));
        return {ok: true, time: (cells[0].innerText||'').trim(), court: header[i]};
      }
    }
  }
  return {ok:false};
})();
"""

BUDDY_JS = """
return (function () {
  const row = document.querySelector('.companion_box .buddy-row');
  if (!row) return 'no-buddy';
  ['mousedown','mouseup','click'].forEach(t => row.dispatchEvent(new MouseEvent(t, {bubbles: true})));
  return (row.innerText || '').trim();
})();
"""


def main():
    config = Config(os.path.join(ROOT, "config.yaml"))
    provider = TTShituProvider.from_config(config)
    print("platform:", provider.username, "typeid", provider.type_id)
    solver = ClickWordSolver(provider=provider)
    local = ClickWordSolver()

    browser = BrowserManager(headless=True, profile_dir=os.path.join(ROOT, ".chrome-profile"), project_root=ROOT)
    driver = browser.start()
    rows = []
    try:
        driver.get(URL)
        time.sleep(8)
        print("date:", driver.execute_script(SET_DATE_JS, DATE))
        time.sleep(4)
        print("pick:", driver.execute_script(PICK_JS))
        time.sleep(0.8)
        print("buddy:", driver.execute_script(BUDDY_JS))
        time.sleep(0.8)

        flow = SubmitCaptchaFlow(driver, solver, local)
        print("patch:", flow.patch())

        button = driver.find_element(By.CSS_SELECTOR, ".submit_order_box .action .btn:not(.cancel)")
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        button.click()
        time.sleep(2.5)

        for index in range(1, ROUNDS + 1):
            data = flow.read()
            if not data or not data.get("image"):
                print(f"#{index}: 没有验证码，重试打开")
                flow.open()
                time.sleep(1.5)
                continue
            driver.execute_script("window.__bbVerify = null;")
            started = time.monotonic()
            captured = flow.pre_solve(max_rounds=1)
            cost = int((time.monotonic() - started) * 1000)
            ok = captured is not None
            rows.append({"round": index, "words": data.get("words"), "ok": ok, "ms": cost})
            print(f"#{index}: {'通过' if ok else '失败'} {cost}ms words={data.get('words')}")
            # 刷新下一张（不提交）
            driver.execute_script("""
            return (function () {
              function walk(vm, d) {
                if (!vm || d > 18) return null;
                if (vm.$data && vm.$data.pointBackImgBase !== undefined) return vm;
                for (const c of (vm.$children || [])) { const h = walk(c, d + 1); if (h) return h; }
                return null;
              }
              for (const el of document.querySelectorAll('*')) { const v = el.__vue__; if (!v) continue; const h = walk(v, 0); if (h) { if (h.refresh) h.refresh(); return true; } }
              return false;
            })();
            """)
            time.sleep(2.0)
    finally:
        driver.quit()

    ok = sum(1 for r in rows if r["ok"])
    print(f"\n汇总: {ok}/{len(rows)} = {ok/len(rows):.0%}" if rows else "无数据")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
