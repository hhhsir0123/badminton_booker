"""下单模块：选场 → 选同伴 → 提交 → 判定回执。

和旧版的区别：
- 用一次 JS 调用来点多个场格，不再每个格子 sleep 1 秒、再点 3 种备选方式；
- 返回 BookingOutcome，不再「点了提交就返回 True」；
- 提交只执行一次，结果不确定时返回 UNKNOWN，交给人工核对，避免重复下单。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .booking_result import BookingOutcome
from .court_query import QueryManager
from .page_js import AWAIT_OUTCOME_JS, SELECT_CELLS_JS, SUBMIT_JS
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

FAILURE_HINTS = ("失败", "已被", "已售", "库存不足", "不可预约", "频繁", "超时", "已过期", "请勿重复", "无库存")
# 注意：不能包含「下单」——"下单失败" 也含这两个字
SUCCESS_HINTS = ("成功", "已生成", "待支付", "去支付", "订单号")


SELECTED_TEXT_JS = """
const box = document.querySelector('.submit_order_box');
return box ? (box.innerText || '') : '';
"""


class BookingManager:
    """预订管理器"""

    def __init__(self, browser, query: QueryManager, captcha_solver=None):
        self.browser = browser
        self.query = query
        self.captcha_solver = captcha_solver
        self.background_clicks = True

    @property
    def driver(self):
        return self.browser.driver

    # ------------------------------------------------------------- 选择

    def select_slots(self, court: str, slots: List[str]) -> Dict:
        """点击场格。返回 {status: 'selected'|'partial'|'unavailable'|'error', detail}。"""
        # 有些平台要求先过验证码才能占场
        if self.captcha_solver is not None:
            try:
                if not self.captcha_solver.solve():
                    logger.error("占场验证码未通过，放弃本次选场")
                    return {"status": "error", "detail": "captcha"}
            except Exception as error:  # noqa: BLE001
                logger.warning("占场验证码处理异常: %s", error)

        # 「取消」在没有已选场格时会退出预约页。
        if not self.clear_selection():
            return {"status": "error", "detail": "selection-clear-failed"}

        if not self.query.ensure_visible(court):
            logger.warning("场地 %s 不在当前页且翻页未找到", court)
            return {"status": "unavailable", "detail": "court-not-found"}

        targets = [{"court": court, "slot": slot} for slot in slots]
        payload = self.driver.execute_async_script(SELECT_CELLS_JS, targets) or {}
        if not payload.get("ok") and payload.get("error") == "no-helpers":
            self.query.install()
            payload = self.driver.execute_async_script(SELECT_CELLS_JS, targets) or {}

        results = payload.get("results") or []
        if not results:
            logger.warning("选场没有返回任何结果，按不可用处理")
            return {"status": "unavailable", "detail": "empty-result"}
        selected, failed = [], []
        for item in results:
            if item.get("ok") and (item.get("changed") or "selected" in (item.get("classes") or [])):
                selected.append(item.get("slot"))
            elif item.get("ok") and item.get("changed") is False:
                # 没变化也可能是「已经是选中态」，再看一眼 class
                failed.append(item)
            else:
                failed.append(item)

        if failed and not selected:
            # 有些前端选中后不会改场格 class，而是把提交按钮点亮。
            # 用「提交按钮是否可用」兜底，避免把已选中的场次误判为不可用。
            if (all(item.get("ok") for item in failed) and self._submit_ready()
                    and self._selected_matches(court, slots, require_details=True)):
                logger.info("场格 class 没有变化，但提交按钮已可用，按已选中处理")
                return {"status": "selected", "detail": []}
            detail = failed[0].get("error") or ",".join(
                item.get("slot", "?") for item in failed
            )
            logger.warning("选中失败: %s", detail)
            return {"status": "unavailable", "detail": detail}
        if failed:
            logger.warning("部分场格没有选中: %s", [item.get("slot") for item in failed])
            return {"status": "partial", "detail": selected, "failed": failed}

        # 页面上的「已选」清单必须和本次候选完全对得上，否则不提交
        if not self._selected_matches(court, slots):
            logger.warning("页面「已选」与候选不一致（%s %s），不提交", court, slots)
            return {"status": "mismatch", "detail": "selected-list-mismatch"}
        return {"status": "selected", "detail": selected}

    def select_segments(self, segments: List[Tuple[str, List[str]]]) -> Dict:
        """一次选中多个场地的场格（可跨场地），返回 {status, detail}。

        跨场地时先切到 A 场所在页选中，再切到 B 场选中；Vue 的「已选」是全局状态，
        翻页不会丢。选完一次提交即可，平台按一张单处理。
        """
        if self.captcha_solver is not None:
            try:
                if not self.captcha_solver.solve():
                    logger.error("占场验证码未通过，放弃本次选场")
                    return {"status": "error", "detail": "captcha"}
            except Exception as error:  # noqa: BLE001
                logger.warning("占场验证码处理异常: %s", error)

        if not self.clear_selection():
            return {"status": "error", "detail": "selection-clear-failed"}

        selected: List[str] = []
        failed: List[Dict] = []
        for court, slots in segments:
            if not self.query.ensure_visible(court):
                logger.warning("场地 %s 不在当前页且翻页未找到", court)
                return {"status": "unavailable", "detail": f"court-not-found:{court}"}
            targets = [{"court": court, "slot": slot} for slot in slots]
            payload = self.driver.execute_async_script(SELECT_CELLS_JS, targets) or {}
            if not payload.get("ok") and payload.get("error") == "no-helpers":
                self.query.install()
                payload = self.driver.execute_async_script(SELECT_CELLS_JS, targets) or {}
            for item in payload.get("results") or []:
                if item.get("ok") and (item.get("changed") or "selected" in (item.get("classes") or [])):
                    selected.append(item.get("slot"))
                else:
                    failed.append(item)

        if failed and not selected:
            if (all(item.get("ok") for item in failed) and self._submit_ready()
                    and self._selected_matches_segments(segments, require_details=True)):
                logger.info("场格 class 没有变化，但提交按钮已可用，按已选中处理")
                return {"status": "selected", "detail": []}
            detail = failed[0].get("error") or ",".join(item.get("slot", "?") for item in failed)
            logger.warning("选中失败: %s", detail)
            return {"status": "unavailable", "detail": detail}
        if failed:
            logger.warning("部分场格没有选中: %s", [item.get("slot") for item in failed])
            return {"status": "partial", "detail": selected, "failed": failed}

        if not self._selected_matches_segments(segments):
            logger.warning("页面「已选」与候选不一致（%s），不提交", segments)
            return {"status": "mismatch", "detail": "selected-list-mismatch"}
        return {"status": "selected", "detail": selected}

    def _selected_matches_segments(self, segments, require_details: bool = False) -> bool:
        """底部「已选」有的版本只显示金额，不写场地/时段明细。

        没有明细可比对时不做判断（和单场地的 _selected_matches 一致），
        只有页面明确写了场地、却和候选对不上时才拦。
        """
        try:
            text = str(self.driver.execute_script(SELECTED_TEXT_JS) or "").strip()
        except Exception:  # noqa: BLE001
            return not require_details
        if not text:
            return not require_details
        courts = [court for court, _ in segments if court]
        mentioned = [court for court in courts if court in text]
        if not mentioned:
            return not require_details
        missing = [court for court in courts if court not in text]
        if missing:
            logger.warning("已选里缺场地: %s", missing)
            return False
        return all(slot in text for court, slots in segments for slot in slots)

    def _describe_submit_state(self) -> str:
        """把订单面板的真实状态打进日志，便于事后定位「须知不可点」这类问题。"""
        script = """
        const boxes = [...document.querySelectorAll('.submit_order_box')];
        return boxes.map(function (b) {
          const btn = [...b.querySelectorAll('.btn,button')].find(x => /^(提交|确认|预约)/.test((x.innerText||'').trim()));
          const terms = [...b.querySelectorAll('input[type=checkbox]')].map(i => ({
              checked: !!i.checked, disabled: !!i.disabled,
              label: i.closest('label') ? i.closest('label').innerText.trim().slice(0, 12) : null }));
          return {visible: !!b.getClientRects().length, cls: String(b.className),
                  btn: btn ? String(btn.className) : null,
                  text: (b.innerText || '').replace(/\s+/g, ' ').slice(0, 40), terms: terms};
        });
        """
        try:
            boxes = self.driver.execute_script(script) or []
        except Exception as error:  # noqa: BLE001
            return f"读取失败: {error}"
        return str(boxes)[:400]

    def _wait_submit_ready(self, timeout_ms: int = 6000) -> bool:
        """等订单面板就绪（签到框可点、按钮可用），最多 timeout_ms 毫秒。"""
        from .page_js import WAIT_SUBMIT_READY_JS

        try:
            result = self.driver.execute_async_script(WAIT_SUBMIT_READY_JS, int(timeout_ms)) or {}
        except Exception as error:  # noqa: BLE001
            logger.debug("等待提交面板就绪失败（忽略）: %s", error)
            return False
        if result.get("ok"):
            return True
        logger.warning("提交面板 %.1fs 内没就绪（%s），仍尝试提交", timeout_ms / 1000, result.get("reason"))
        return False

    def clear_selection(self) -> bool:
        """只清理已选场格，并确认仍在同一张预约表。"""
        try:
            from selenium.webdriver.common.by import By

            selected = ".reserveBlock.selected, .reserveBlock.active"
            if not self.driver.execute_script(
                "return Boolean(document.querySelector(arguments[0]));", selected
            ):
                return True

            wanted = self.query.last_date
            button = self.driver.find_element(
                By.CSS_SELECTOR, ".submit_order_box .action .btn.cancel"
            )
            self.driver.execute_script("arguments[0].click();", button)
            return self.query._wait_false(
                lambda _: (
                    (not wanted or self.query.is_active(wanted))
                    and self.query.snapshot()
                    and not self.driver.execute_script(
                        "return Boolean(document.querySelector(arguments[0]));", selected
                    )
                ),
                2,
            )
        except Exception:  # noqa: BLE001
            return False

    def _selected_matches(self, court: str, slots: List[str], require_details: bool = False) -> bool:
        """读页面底部的「已选」文案核对；页面没写明细时不做判断，退回场格 class。

        只有页面明确写了「场地/时段」时才核对，避免因为拿不到文案而误伤正常下单。
        """
        try:
            text = str(self.driver.execute_script(SELECTED_TEXT_JS) or "").strip()
        except Exception:  # noqa: BLE001
            return not require_details
        has_court = bool(court) and court in text
        has_slot = [slot in text for slot in slots]
        if not has_court and not any(has_slot):
            return not require_details
        return has_court and all(has_slot)

    def _submit_ready(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                "return Boolean(window.__bb && window.__bb.submitEnabled(window.__bb.submitButton()));"
            ))
        except Exception:  # noqa: BLE001
            return False

    def click_partner(self, partner_names: List[str]) -> bool:
        """选择同行人；失败不阻断下单，只记录。"""
        names = [name.strip() for name in partner_names if name and name.strip()]
        if not names:
            return True
        result = self.driver.execute_script(
            """
            const [names] = arguments;
            const visible = e => e && e.getClientRects().length;
            const rows = [...document.querySelectorAll('.buddy-box .buddy-row')].filter(visible);
            const clicked = [];
            for (const name of names) {
                const row = rows.find(r => (r.innerText || '').indexOf(name) >= 0);
                if (!row) { continue; }
                row.click();
                clicked.push(name);
            }
            return { clicked: clicked, total: rows.length };
            """,
            names,
        ) or {}
        clicked = result.get("clicked") or []
        missing = [name for name in names if name not in clicked]
        if missing:
            logger.warning("未找到同伴: %s（页面共 %s 行）", missing, result.get("total"))
        else:
            logger.info("已选择同伴: %s", clicked)
        return not missing

    # ------------------------------------------------------------- 提交

    def submit(self, timeout_ms: int = 6000, *, confirm_terms: bool = False) -> Dict:
        return self.driver.execute_async_script(SUBMIT_JS, int(timeout_ms), confirm_terms) or {
            "ok": False, "reason": "no-result"
        }

    def _verdict(self, payload: Dict) -> BookingOutcome:
        reason = payload.get("reason")
        message = str(payload.get("message") or "")

        if reason == "navigated":
            # 只有跳到订单/支付页才算成功；跳登录页（会话过期）必须判为未知
            url = str(payload.get("url") or self.driver.current_url or "").lower()
            if any(token in url for token in ("order", "pay", "success")):
                return BookingOutcome.CREATED
            logger.warning("页面跳转到 %s，不是订单页，结果未知", url)
            return BookingOutcome.UNKNOWN

        if payload.get("ok"):
            logger.info("平台回执: %s %s", reason, message)
            return BookingOutcome.CREATED

        if reason == "toast":
            if any(hint in message for hint in FAILURE_HINTS):
                logger.warning("平台拒绝: %s", message)
                return BookingOutcome.REJECTED
            if any(hint in message for hint in SUCCESS_HINTS):
                return BookingOutcome.CREATED

        if reason == "submit-disabled":
            return BookingOutcome.UNAVAILABLE
        if reason in ("no-submit-box", "no-submit-button"):
            # 可能已经跳转到订单页
            if any(token in self.driver.current_url.lower() for token in ("order", "pay")):
                return BookingOutcome.CREATED
            return BookingOutcome.UNKNOWN
        return BookingOutcome.UNKNOWN

    # ------------------------------------------------------------- 主流程

    def book_court(
        self,
        court_name: str,
        time_slots: List[str],
        partner_names: Optional[List[str]] = None,
        is_click_parter: bool = False,
        submit_timeout_ms: int = 6000,
        allow_partial: bool = False,
        *,
        confirm_terms: bool = False,
    ) -> BookingOutcome:
        """提交一次预约，返回平台回执判定。"""
        partner_names = partner_names or []
        logger.info("尝试预订 %s %s", court_name, ", ".join(time_slots))

        selection = self.select_slots(court_name, time_slots)
        if selection["status"] == "error":
            logger.warning("选场阶段出错（%s），不提交订单", selection.get("detail"))
            return BookingOutcome.UNKNOWN
        if selection["status"] == "unavailable":
            logger.info("场格不可用（%s），换下一个候选", selection.get("detail"))
            return BookingOutcome.UNAVAILABLE
        if selection["status"] == "mismatch":
            logger.warning("页面上已选的场次和候选不一致，不提交")
            return BookingOutcome.UNAVAILABLE
        if selection["status"] == "partial" and not allow_partial:
            logger.info("只选中部分时段且未开启 allow_partial，换下一个候选")
            return BookingOutcome.UNAVAILABLE

        if not self.background_clicks:
            # 保留一个显式等待的钩子，方便需要人工确认弹窗的场景
            time.sleep(0.2)

        if is_click_parter or partner_names:
            self.click_partner(partner_names)

        # 放场瞬间整页刷新后，订单面板可能还在初始化（须知 disabled），先等它就绪
        self._wait_submit_ready(timeout_ms=max(2000, submit_timeout_ms))

        # 放场瞬间刚整页刷新过，预约须知/提交按钮可能还是 disabled 状态，
        # prepareSubmit 会直接返回 terms-unchecked。什么都没提交，重试是安全的。
        payload = {}
        for attempt in range(1, 6):
            payload = (self.submit(submit_timeout_ms, confirm_terms=True)
                       if confirm_terms else self.submit(submit_timeout_ms))
            reason = payload.get("reason")
            if reason not in ("terms-unchecked", "terms-confirmation-failed", "submit-disabled"):
                break
            if attempt < 5:
                delay = 0.4 * attempt
                logger.warning("提交按钮/预约须知未就绪（%s），%.1fs 后重试（%s/5）",
                               reason, delay, attempt)
                time.sleep(delay)
        if payload.get("reason") == "captcha" and confirm_terms:
            logger.warning("平台要求验证码，暂停自动下单，请在预约页面完成验证")
            return BookingOutcome.UNKNOWN
        if payload.get("reason") == "captcha":
            payload = self._solve_captcha_and_wait(submit_timeout_ms)
        if payload.get("reason") in ("terms-unchecked", "terms-confirmation-failed", "submit-disabled"):
            logger.warning("提交面板诊断: %s", self._describe_submit_state())
            # 这类失败一定没提交出去，是页面还没就绪，不是「结果未知」：
            # 返回 UNAVAILABLE，让上层换下一个候选继续抢（每次尝试都多一次机会）。
            logger.warning("预约须知/提交按钮始终未就绪（%s），订单未提交，换下一个候选",
                           payload.get("reason"))
            return BookingOutcome.UNAVAILABLE
        outcome = self._verdict(payload)
        if outcome is BookingOutcome.UNKNOWN:
            self._snapshot_unknown()
        return outcome

    def _solve_captcha_and_wait(self, timeout_ms: int) -> Dict:
        """提交后弹出点选验证码：本地识别 → 点击 → 回到「只观察结果」，绝不重复提交。"""
        logger.info("提交后出现点选验证码，开始识别（首次加载模型约 1~2 秒）")
        try:
            from ..utils.click_word_solver import ClickWordSolver
            from .submit_captcha import SubmitCaptchaFlow

            flow = SubmitCaptchaFlow(self.driver, ClickWordSolver())
            if flow.solve_now(max_rounds=2) is None:
                return {"ok": False, "reason": "captcha", "message": "验证码处理失败"}
            logger.info("验证码已通过，继续等待提交回执")
            return self.driver.execute_async_script(AWAIT_OUTCOME_JS, int(timeout_ms), "") or {
                "ok": False, "reason": "no-result"
            }
        except Exception as error:  # noqa: BLE001
            logger.error("验证码处理异常: %s", error, exc_info=True)
            return {"ok": False, "reason": "captcha", "message": str(error)}

    def _snapshot_unknown(self):
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = f"logs/unknown_{stamp}.png"
            self.driver.save_screenshot(path)
            logger.warning("提交结果不确定，已截图: %s（请在浏览器里核对订单）", path)
        except Exception as error:  # noqa: BLE001
            logger.debug("保存截图失败: %s", error)
