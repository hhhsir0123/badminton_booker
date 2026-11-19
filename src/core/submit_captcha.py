"""下单验证码（AJ-Captcha clickWord）的预解与代提交。

2026-11-19 真机验证通过的流程：

    1. 打开验证码（不需要先选场）：doVerify(true)
    2. 读取图片 + 目标词（pointBackImgBase / poinTextList）
    3. 识别坐标（本地 OCR 或图鉴），点击 canvasClick({offsetX, offsetY})
    4. 劫持 verifySuccess：只保存 captchaVerification / captchaToken / submitForm，
       不让它立刻提交（默认行为会立即 submitData）
    5. 稍后用 token + 真实表单调用 submitData 完成下单

实测：验证码在 T 时刻解开，10 秒后仍可用来下单成功
（tradeNo D261119000236，2026-11-21 08:00-09:00）。
服务端二次校验 token 的有效期是 3 分钟（EXPIRESIN_THREE=180s）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

FIND_HELPERS = """
function __bbFindVerify() {
  function walk(vm, depth) {
    if (!vm || depth > 18) { return null; }
    if (vm.$data && vm.$data.pointBackImgBase !== undefined) { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) {
    const vm = el.__vue__; if (!vm) { continue; }
    const hit = walk(vm, 0); if (hit) { return hit; }
  }
  return null;
}
function __bbFindForm() {
  function walk(vm, depth) {
    if (!vm || depth > 20) { return null; }
    if (typeof vm.submitData === 'function' && typeof vm.verifySuccess === 'function') { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) {
    const vm = el.__vue__; if (!vm) { continue; }
    const hit = walk(vm, 0); if (hit) { return hit; }
  }
  return null;
}
"""

OPEN_JS = FIND_HELPERS + """
return (function () {
  function walk(vm, depth) {
    if (!vm || depth > 16) { return null; }
    if (typeof vm.doVerify === 'function') { return vm; }
    for (const child of (vm.$children || [])) { const hit = walk(child, depth + 1); if (hit) { return hit; } }
    return null;
  }
  for (const el of document.querySelectorAll('*')) {
    const vm = el.__vue__; if (!vm) { continue; }
    const hit = walk(vm, 0);
    if (hit) { hit.doVerify(true); return true; }
  }
  return false;
})();
"""

READ_JS = FIND_HELPERS + """
return (function () {
  const vm = __bbFindVerify();
  if (!vm) { return null; }
  return {image: vm.$data.pointBackImgBase, words: vm.$data.poinTextList,
          setSize: vm.$data.setSize, text: vm.$data.text || ''};
})();
"""

PATCH_JS = FIND_HELPERS + """
return (function () {
  const form = __bbFindForm();
  if (!form) { return 'no-form'; }
  if (!form.__bbOriginalVerifySuccess) { form.__bbOriginalVerifySuccess = form.verifySuccess; }
  window.__bbVerify = null;
  form.verifySuccess = function (verification, token) {
    window.__bbVerify = {verification: verification, token: token,
                         form: JSON.parse(JSON.stringify(form.submitForm || {}))};
  };
  return 'patched';
})();
"""

CLICK_JS = FIND_HELPERS + """
return (function (points) {
  const vm = __bbFindVerify();
  if (!vm) { return 'no-vm'; }
  const size = vm.$data.setSize || {};
  const width = parseInt(size.imgWidth) || 310;
  const height = parseInt(size.imgHeight) || 155;
  for (const point of points) {
    vm.canvasClick({offsetX: point[0] * width / 310, offsetY: point[1] * height / 155});
  }
  return 'clicked';
})(arguments[0]);
"""

CAPTURED_JS = "return window.__bbVerify || null;"

RESTORE_JS = FIND_HELPERS + """
const form = __bbFindForm();
if (form && form.__bbOriginalVerifySuccess) {
  form.verifySuccess = form.__bbOriginalVerifySuccess;
  delete form.__bbOriginalVerifySuccess;
}
window.__bbVerify = null;
return true;
"""

SUBMIT_JS = FIND_HELPERS + """
return (function (payload) {
  const form = __bbFindForm();
  if (!form) { return 'no-form'; }
  form.submitData(payload);
  return 'sent';
})(arguments[0]);
"""

MATERIAL_JS = "return { url: location.href, body: (document.body && document.body.innerText || '').slice(-400) };"

READ_FORM_JS = FIND_HELPERS + """
return (function () {
  const form = __bbFindForm();
  if (!form) { return null; }
  return JSON.parse(JSON.stringify(form.submitForm || {}));
})();
"""


@dataclass
class PreSolvedCaptcha:
    verification: str
    token: str
    form: Dict[str, object] = field(default_factory=dict)
    solved_at: float = field(default_factory=time.time)

    def age(self) -> float:
        return time.time() - self.solved_at

    def payload(self, overrides: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """合并表单 + token，overrides 优先（用于换成真实场次）。"""
        data = dict(self.form)
        if overrides:
            data.update(overrides)
        data["captchaVerification"] = self.verification
        data["captchaToken"] = self.token
        return data


class SubmitCaptchaFlow:
    """驱动页面上的点选验证码，支持"先解后提交"。"""

    def __init__(self, driver, solver=None, local_solver=None,
                 ttl_seconds: float = 170.0, agreement_px: int = 40):
        self.driver = driver
        self.solver = solver
        self.local_solver = local_solver
        self.ttl_seconds = ttl_seconds
        self.agreement_px = int(agreement_px)
        self.presolved: Optional[PreSolvedCaptcha] = None

    # ------------------------------------------------------------- 基础

    def open(self) -> bool:
        try:
            return bool(self.driver.execute_script(OPEN_JS))
        except Exception as error:  # noqa: BLE001
            logger.warning("打开验证码失败: %s", error)
            return False

    def read(self) -> Optional[Dict]:
        try:
            return self.driver.execute_script(READ_JS)
        except Exception as error:  # noqa: BLE001
            logger.warning("读取验证码失败: %s", error)
            return None

    def patch(self) -> bool:
        try:
            return self.driver.execute_script(PATCH_JS) == "patched"
        except Exception as error:  # noqa: BLE001
            logger.warning("劫持 verifySuccess 失败: %s", error)
            return False

    def click(self, points: Sequence[Tuple[int, int]]) -> bool:
        try:
            return self.driver.execute_script(CLICK_JS, [list(p) for p in points]) == "clicked"
        except Exception as error:  # noqa: BLE001
            logger.warning("点击验证码失败: %s", error)
            return False

    def captured(self) -> Optional[PreSolvedCaptcha]:
        try:
            data = self.driver.execute_script(CAPTURED_JS)
        except Exception:  # noqa: BLE001
            return None
        if not data or not data.get("verification") or not data.get("token"):
            return None
        return PreSolvedCaptcha(
            verification=data["verification"],
            token=data.get("token") or "",
            form=data.get("form") or {},
        )

    def ready_for_submission(self, min_validity: float = 0) -> bool:
        """结果必须完整、未过期，且仍属于当前页面；刷新后不能复用旧结果。"""
        captcha = self.presolved
        if (captcha is None or not captcha.verification or not captcha.token
                or not 0 <= captcha.age() < self.ttl_seconds - max(0, min_validity)):
            return False
        try:
            current = self.driver.execute_script(CAPTURED_JS) or {}
            return (current.get("verification") == captcha.verification
                    and current.get("token") == captcha.token)
        except Exception:  # noqa: BLE001
            return False

    def discard(self) -> bool:
        """清除结果并恢复正常验证码回调，避免兜底时验证成功却无法提交。"""
        self.presolved = None
        try:
            return bool(self.driver.execute_script(RESTORE_JS))
        except Exception as error:  # noqa: BLE001
            logger.warning("恢复现场验证码回调失败: %s", type(error).__name__)
            return False

    # ------------------------------------------------------------- 主流程

    def pre_solve(self, max_rounds: int = 3) -> Optional[PreSolvedCaptcha]:
        """打开验证码 → 识别 → 点击 → 保存 token（不提交）。

        多重保护：
        - 已有验证码就不再触发 doVerify（避免白拿一张）；
        - 每一轮失败就换图重试（认不出不计失败次数）；
        - 平台坐标和本地 OCR 差得太远时不点，直接换图（避免点错被计入失败）。
        """
        self.presolved = None
        if self.solver is None:
            logger.warning("没有识别器，无法预解验证码")
            return None
        if not self.patch():
            logger.warning("页面结构不符合预期，放弃预解")
            return None

        try:
            return self._pre_solve_rounds(max_rounds)
        finally:
            if self.presolved is None:
                self.discard()

    def _pre_solve_rounds(self, max_rounds: int) -> Optional[PreSolvedCaptcha]:
        if not (self.read() or {}).get("image"):
            if not self.open():
                logger.info("打不开验证码（可能要等提交时才出现）")
                return None
        # doVerify 异步加载图片，不能把组件已存在误当作图片已就绪。
        deadline = time.monotonic() + 3
        while not (self.read() or {}).get("image"):
            if time.monotonic() >= deadline:
                logger.warning("等待下单验证码图片超时")
                return None
            time.sleep(0.05)

        for round_index in range(1, max_rounds + 1):
            data = self.read()
            if not data or not data.get("image"):
                logger.warning("预解第 %s 轮：没拿到验证码图片", round_index)
                return None
            import base64

            png = base64.b64decode(data["image"].split(",", 1)[-1])
            started = time.monotonic()
            points = self.solver.solve(png, data.get("words") or [])
            cost = int((time.monotonic() - started) * 1000)
            if not points:
                logger.warning("预解第 %s 轮：识别不全（%sms），换一张", round_index, cost)
                self._refresh()
                time.sleep(1.2)
                continue
            if not self._agree_with_local(png, points, data.get("words") or []):
                logger.warning("预解第 %s 轮：平台坐标和本地差异过大，不冒险点击，换一张", round_index)
                self._refresh()
                time.sleep(1.2)
                continue
            if not self.click(points):
                return None
            time.sleep(0.6)
            captured = self.captured()
            if captured:
                captured.solved_at = time.time()
                self.presolved = captured
                logger.info("验证码预解成功（%sms）", cost)
                return captured
            logger.warning("预解第 %s 轮：点击后没有拿到 token（可能没通过）", round_index)
            self._refresh()
            time.sleep(1.2)
        return None

    def _agree_with_local(self, png: bytes, points, words) -> bool:
        """平台坐标与本地 OCR 的一致性检查；没有本地识别器时直接放行。"""
        if self.local_solver is None or not points:
            return True
        try:
            local = self.local_solver.solve(png, words)
        except Exception:  # noqa: BLE001
            return True
        if not local or len(local) != len(points):
            return True
        worst = max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, local))
        logger.info("平台与本地坐标最大差异 %spx", worst)
        return worst <= self.agreement_px

    def submit(self, overrides: Optional[Dict[str, object]] = None) -> bool:
        """用预解 token + 表单提交。没有预解结果时返回 False。"""
        if not self.ready_for_submission():
            logger.warning("预解结果缺失、过期或页面已刷新，必须现场重新验证")
            return False
        payload = self.presolved.payload(overrides)
        try:
            result = self.driver.execute_script(SUBMIT_JS, payload)
        except Exception as error:  # noqa: BLE001
            logger.warning("代提交失败: %s", error)
            return False
        logger.info("代提交结果: %s", result)
        if result == "sent":
            self.discard()
        return result == "sent"

    def _refresh(self) -> None:
        try:
            self.driver.execute_script(FIND_HELPERS + """
            (function () {
              const vm = __bbFindVerify();
              if (vm && vm.refresh) { vm.refresh(); return true; }
              return false;
            })();
            """)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- 代提交

    def read_form(self) -> Optional[Dict[str, object]]:
        """读取当前待提交的表单（submitForm）。"""
        try:
            return self.driver.execute_script(READ_FORM_JS)
        except Exception as error:  # noqa: BLE001
            logger.warning("读取提交表单失败: %s", error)
            return None

    def click_submit_button(self) -> bool:
        """用 Selenium 原生点击"提交"（触发 doVerify 并填充 submitForm）。"""
        try:
            from selenium.webdriver.common.by import By

            button = self.driver.find_element(
                By.CSS_SELECTOR, ".submit_order_box .action .btn:not(.cancel)"
            )
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            button.click()
            return True
        except Exception as error:  # noqa: BLE001
            logger.warning("点击提交按钮失败: %s", error)
            return False

    def submit_selected(self, overrides: Optional[Dict[str, object]] = None) -> bool:
        """选场之后：点提交唤起验证码 → 读表单 → 用预解 token 直接提交。"""
        if not self.ready_for_submission():
            return False
        # 即使验证码组件已经存在，也要点击提交以生成本次真实选场和同伴表单。
        if not self.click_submit_button():
            return False
        form = self.read_form()
        if not form:
            logger.warning("没有读取到本次选场表单，放弃预解提交")
            return False
        merged = dict(form)
        merged.update(overrides or {})
        return self.submit(merged)

    def wait_success(self, timeout: float = 6.0) -> bool:
        """提交后等页面出现「提交预约成功」或其他结果提示。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                text = self.driver.execute_script(
                    "return (document.body && document.body.innerText || '').slice(-600);"
                ) or ""
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
                continue
            if "提交预约成功" in text:
                return True
            if "失败" in text or "已满" in text or "不可预约" in text:
                return False
            time.sleep(0.2)
        return False

    # ------------------------------------------------------------- 现场兜底

    def solve_now(self, max_rounds: int = 3) -> Optional[List[Tuple[int, int]]]:
        """现场解一张：返回坐标并完成点击（不依赖预解）。"""
        for round_index in range(1, max_rounds + 1):
            data = self.read()
            if not data or not data.get("image"):
                return None
            import base64

            png = base64.b64decode(data["image"].split(",", 1)[-1])
            points = self.solver.solve(png, data.get("words") or []) if self.solver else None
            if points and self.click(points):
                return points
            logger.warning("现场解第 %s 轮失败，换一张", round_index)
            self._refresh()
            time.sleep(1.2)
        return None
