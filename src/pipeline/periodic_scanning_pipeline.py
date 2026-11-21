"""空场轮询：循环查询未来若干天的预约表，发现符合条件的空场就立即下单/通知。

和「抢票」共用同一条下单链路，轮询不是「只看表」的旁观者：

- 发现空场后**同一轮内**就选场、点提交，不等到下一个 tick；
- **不预解验证码**：等提交后验证码真的弹出来，再由 ``SubmitCaptchaFlow``
  现场识别（本地 OCR / 图鉴），带 token 提交；
- 识别失败只换图重试「没认出来」的情况；一旦点过没通过就不再点，
  避免触发 AJ-Captcha 的失败锁；没解开时 10 秒后再扫一轮；
- 连续 3 次解不开、或平台提示失败次数过多，就停止轮询并通知，不硬刚风控；
- 抢到后发飞书提醒 15 分钟内付款，然后每 60 秒查一次支付状态：
  没付就提醒，付了就结束任务并关浏览器，15 分钟没付则订单失效、重新抢占；
- 连续读不到预约表时退避重试，并尝试重新登录恢复会话。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..core.booking_result import BookingOutcome
from ..core.court_query import BEIJING, parse_slot, slot_key
from .base_pipeline import BasePipeline

# 平台要求抢到后 15 分钟内支付，超时订单会被取消
PAYMENT_WINDOW_MINUTES = 15
# 抢到后每 60 秒查一次支付状态，没付就发飞书提醒
PAYMENT_CHECK_SECONDS = 60
# 验证码没解开时不要干等一个完整轮询间隔，空场还在就赶紧再试一次
CAPTCHA_RETRY_SECONDS = 10


class PeriodicScanningPipeline(BasePipeline):
    """周期性扫描流程"""

    def __init__(self, config_path: Optional[str] = None):
        super().__init__(config_path)
        self.scan_interval = int(self.config.get("polling.polling_interval", 60) or 60)
        self.polling_time_range = str(self.config.get("polling.polling_time_range", "") or "").strip()
        try:
            self.polling_hours = int(self.config.get("polling.polling_hours", 1) or 1)
        except (TypeError, ValueError):
            self.polling_hours = 1
        if self.polling_hours not in (1, 2):
            self.polling_hours = 1
        # 兼容旧配置：只有 polling_time_slots 时，取「最早开始 ~ 最晚结束」作为时间段
        if not self.polling_time_range:
            legacy = self.config.get("polling.polling_time_slots", []) or []
            keys = [slot_key(str(item).replace("～", "-").replace("－", "-")) for item in legacy]
            keys = [key for key in keys if key]
            if keys:
                self.polling_time_range = f"{min(key[:5] for key in keys)}-{max(key[6:] for key in keys)}"
        self.polling_time_slots = []
        self.polling_dates = self.config.get("polling.polling_dates")
        self.polling_date = self.config.get("polling.polling_date", "") or ""
        self.polling_date_offsets = self.config.get("polling.polling_date_offsets", [0, 1, 2]) or [0]
        courts = self.config.get("polling.polling_courts", []) or []
        # 「全部场地」是显式的不限选项，等价于空列表
        self.polling_courts = [
            str(court).strip() for court in courts
            if str(court).strip() and str(court).strip() != "全部场地"
        ]
        self.is_auto_booking = bool(self.config.get("polling.is_auto_booking", False))
        self.partner_name = self.config.get("user.partner_name", "") or ""
        self.venue_type = self.config.get("booking.venue_type", "羽毛球")
        self.is_running = False
        self.summary = {"state": "idle", "mode": "poll"}
        # 下单验证码：和抢票共用 BasePipeline._build_submit_flow
        self.submit_captcha = None
        self._seen: Dict[str, tuple] = {}   # 每个日期上次推送空场的签名（去重）
        self._failures = 0
        self._captcha_failures = 0
        self._retry_soon = False
        self._halt = False
        self._paid = False
        self._booked: List[Dict[str, object]] = []
        # 支付等待的参数（测试里可以调小）
        self.payment_window_seconds = PAYMENT_WINDOW_MINUTES * 60
        self.payment_check_seconds = PAYMENT_CHECK_SECONDS

    # ------------------------------------------------------------- 生命周期

    def start(self) -> bool:
        """启动浏览器后立刻把验证码组件准备好，别等发现空场才加载模型。"""
        if not super().start():
            return False
        self._prepare_submit_captcha()
        return True

    def _prepare_submit_captcha(self):
        """只把识别组件和模型准备好；轮询不预解，查到空场后现场再解。"""
        if getattr(self, "submit_captcha", None) is not None:
            return
        try:
            self.submit_captcha = self._build_submit_flow()
            self.logger.info("点选验证码识别组件已就绪（查到空场后再现场解）")
        except Exception as error:  # noqa: BLE001
            self.submit_captcha = None
            self.logger.warning("验证码识别组件初始化失败（%s），下单时只能走兜底", type(error).__name__)

    def _captcha_flow(self):
        """懒加载：浏览器已经起来但没有组件时再试一次。"""
        flow = getattr(self, "submit_captcha", None)
        if flow is not None:
            return flow
        if getattr(self, "browser", None) is None:
            return None
        try:
            flow = self._build_submit_flow()
        except Exception as error:  # noqa: BLE001
            self.logger.warning("验证码识别组件不可用（%s），下单时只能走兜底", type(error).__name__)
            return None
        self.submit_captcha = flow
        return flow

    def cleanup(self):
        flow = getattr(self, "submit_captcha", None)
        if flow is not None:
            try:
                flow.discard()
            except Exception:  # noqa: BLE001
                pass
            self.submit_captcha = None
        super().cleanup()

    # ------------------------------------------------------------- 配置校验

    def _validate_config(self) -> bool:
        if not getattr(self, "polling_time_slots", None) and not getattr(self, "is_auto_booking", False):
            self.logger.warning("未配置轮询时段，将只读表不下单")
        return True

    def _dates(self) -> List[str]:
        selected = getattr(self, "polling_dates", None)
        if selected is not None:
            today = datetime.now(BEIJING).date().isoformat()
            return sorted({str(day) for day in selected if str(day) >= today})
        explicit = getattr(self, "polling_date", "")
        if explicit:
            return [explicit]
        offsets = getattr(self, "polling_date_offsets", [0]) or [0]
        today = datetime.now(BEIJING).date()
        return [(today + timedelta(days=int(offset))).isoformat() for offset in offsets]

    # ------------------------------------------------------------- 查询

    def _query_available_courts(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        """按日期返回可订场地：{date: {court: {slots: 页面原文}}}。

        用 scan() 而不是 query_available_courts()，是因为下单要把「页面原文」时段
        传给选场脚本（页面可能写 19:00～20:00，而我们内部用 19:00-20:00）。
        不同日期分开存，避免被当成同一场地连续两小时。
        """
        by_date: Dict[str, Dict[str, Dict[str, str]]] = {}
        self.summary["unread_dates"] = []
        for day in self._dates():
            try:
                inventory = self.query_manager.scan(day)
            except Exception as error:  # noqa: BLE001
                self.logger.error("查询 %s 失败: %s", day, error)
                self.summary["unread_dates"].append(day)
                continue
            status = getattr(self.query_manager, "last_query_status", {})
            if isinstance(status, dict) and status.get("ok") is False:
                self.summary["unread_dates"].append(day)
                self.logger.warning("%s 的预约表未读到（%s）", day, status.get("error"))
                continue
            if not inventory:
                continue
            by_date[day] = {
                court: dict(slots) for court, slots in (inventory or {}).items() if slots
            }
        return by_date

    # ------------------------------------------------------------- 单轮扫描

    def _perform_scan(self) -> bool:
        """跑一轮：读当前羽毛球预约表 → 找连续空场 → 下单/通知。"""
        if self.stopped or getattr(self, "_halt", False) or getattr(self, "_paid", False):
            return True
        return self._scan_site()

    def _scan_site(self) -> bool:
        """扫一遍当前羽毛球预约表的所有日期。返回「是否所有日期都读到了」。"""
        by_date = self._query_available_courts()
        self.summary["last_scan"] = datetime.now(BEIJING).isoformat(timespec="seconds")
        unread = self.summary.get("unread_dates") or []

        if not by_date:
            self._clear_seen()
            if unread:
                self.logger.warning("本轮有日期未读到，无法确认全部日期的空场情况：%s", ", ".join(unread))
                return False
            # 读表成功、只是没有空场：这不算失败，不能触发退避/重登录
            self.logger.info("本轮没有可用场地")
            self.summary["available"] = {}
            return True

        self.summary["available"] = {day: len(courts) for day, courts in by_date.items()}
        for day, inventory in by_date.items():
            if self.stopped or getattr(self, "_halt", False):
                break
            inventory = self._filter_courts(inventory)
            self.logger.info("%s 有 %s 个关注场地可订", day, len(inventory))
            windows = self._find_windows(inventory)
            if not windows:
                self.logger.info(
                    "%s 在 %s 内没有连续 %s 小时的空场",
                    day, self.polling_time_range or "全天", self.polling_hours,
                )
                self._forget(day)
                continue
            self.logger.info("%s 找到 %s 个连续 %s 小时候选，最优：%s",
                             day, len(windows), self.polling_hours,
                             self._describe_window(windows[0]))
            self._send_availability(day, windows[0])
            if not (self.is_auto_booking and not self.stopped):
                continue
            if not self.query_manager.is_active(day) and not self.query_manager.focus_date(day):
                self.logger.warning("%s 切不回目标日期，跳过下单", day)
                continue
            booked = self._book_windows(day, windows)
            if booked or getattr(self, "_paid", False):
                self.summary["booked"] = list(self._booked_list())
                return True
        return not unread

    # ------------------------------------------------------------- 抢占式下单

    # ------------------------------------------------------------- 连续小时窗口

    def _window_slots(self) -> List[str]:
        """把配置的时间段拆成整点时段：15:00-21:00 → 15:00-16:00 … 20:00-21:00。"""
        bounds = parse_slot(self.polling_time_range or "")
        if not bounds:
            return []
        start, end = bounds
        if not start.endswith(":00") or not end.endswith(":00"):
            return []
        return [f"{hour:02d}:00-{hour + 1:02d}:00"
                for hour in range(int(start[:2]), int(end[:2]))]

    def _filter_courts(self, inventory):
        if not self.polling_courts:
            return inventory
        return {court: slots for court, slots in (inventory or {}).items()
                if court in self.polling_courts}

    def _find_windows(self, inventory) -> List[List[Tuple[str, str, str]]]:
        """在关注时间段内找连续 N 小时的空场。

        每个窗口是 [(场地, 时段键, 页面原文)]。同一场地能凑齐就优先同一场地，
        凑不齐才允许跨场地（例如 15 点 A 场 + 16 点 B 场）。
        """
        slots = self._window_slots()
        if not slots:
            return []
        courts = [court for court in (inventory or {}) if not self.polling_courts or court in self.polling_courts]
        free = {court: set((inventory.get(court) or {})) for court in courts}
        hours = max(1, min(int(getattr(self, "polling_hours", 1) or 1), 2))

        def label(court: str, slot: str) -> str:
            return (inventory.get(court) or {}).get(slot, slot)

        windows: List[List[Tuple[str, str, str]]] = []
        if hours == 1:
            for slot in slots:
                for court in courts:
                    if slot in free[court]:
                        windows.append([(court, slot, label(court, slot))])
            return windows

        same_court: List[List[Tuple[str, str, str]]] = []
        for court in courts:
            for index in range(len(slots) - 1):
                first, second = slots[index], slots[index + 1]
                if first in free[court] and second in free[court]:
                    same_court.append([
                        (court, first, label(court, first)),
                        (court, second, label(court, second)),
                    ])
        if same_court:
            # 先按开始时间、再按场地排序，保证「最优」是时间最早的那个窗口
            same_court.sort(key=lambda window: (slots.index(window[0][1]), window[0][0]))
            return same_court

        cross: List[List[Tuple[str, str, str]]] = []
        for index in range(len(slots) - 1):
            first, second = slots[index], slots[index + 1]
            for court_a in courts:
                if first not in free[court_a]:
                    continue
                for court_b in courts:
                    if court_b == court_a or second not in free[court_b]:
                        continue
                    cross.append([
                        (court_a, first, label(court_a, first)),
                        (court_b, second, label(court_b, second)),
                    ])
        cross.sort(key=lambda window: (slots.index(window[0][1]), window[0][0]))
        return cross

    @staticmethod
    def _describe_window(window: List[Tuple[str, str, str]]) -> str:
        return " + ".join(f"{court} {slot}" for court, slot, _ in window)

    # ------------------------------------------------------------- 抢占式下单

    def _book_windows(self, day: str, windows: List[List[Tuple[str, str, str]]]) -> List[Tuple[str, List[str]]]:
        """按优先级尝试每个连续窗口；抢到就等支付，失败换下一个窗口。"""
        for window in windows:
            if self.stopped or getattr(self, "_halt", False):
                return []
            segments = self._book_window(day, window)
            if not segments:
                if getattr(self, "_halt", False):
                    return []
                continue
            booking = {
                "day": day,
                "segments": [{"court": court, "slots": list(slots)} for court, slots in segments],
            }
            self.logger.info("预订成功（订单已创建）：%s，进入支付等待",
                             self._describe_booking(booking))
            self._notify_booking_success(booking)
            result = self._await_payment(booking)
            if result == "paid":
                self._booked_list().append(booking)
                self._paid = True
                self.summary["message"] = f"已支付：{self._describe_booking(booking)}，轮询结束"
                self.logger.info("订单已支付，轮询任务结束")
                return segments
            if result == "expired":
                self.logger.warning("订单 %s 分钟内没支付，已失效，重新开始抢", PAYMENT_WINDOW_MINUTES)
                self.summary["message"] = "订单未支付已失效，重新抢占"
                self._notify_failure(
                    f"{self._describe_booking(booking)} 未在 {PAYMENT_WINDOW_MINUTES} 分钟内支付，"
                    "订单已失效，继续抢占"
                )
                self._retry_soon = True
            return []
        return []

    def _book_window(self, day: str, window: List[Tuple[str, str, str]]) -> List[Tuple[str, List[str]]]:
        """把一个连续小时窗口落成**一张单**：两个格子一起选、只提交一次。"""
        partners = [self.partner_name] if self.partner_name else []
        grouped_labels: Dict[str, List[str]] = {}
        grouped_keys: Dict[str, List[str]] = {}
        for court, slot, label in window:
            grouped_labels.setdefault(court, []).append(label)
            grouped_keys.setdefault(court, []).append(slot)
        self.logger.info("发现连续空场，提交一单：%s", self._describe_window(window))
        outcome = self._book_one(list(grouped_labels.items()), partners)
        self._handle_window_outcome(outcome, self._describe_window(window),
                                    [slot for _, slot, _ in window])
        if outcome is BookingOutcome.CREATED:
            return list(grouped_keys.items())
        return []

    def _handle_window_outcome(self, outcome, name: str, slots: List[str]):
        if outcome is None:
            if getattr(self, "_miss_reason", "captcha") == "captcha":
                self._on_captcha_miss(name, slots)
            else:
                self.logger.warning("提交按钮/表单没就绪（%s），本轮放弃，稍后重试",
                                    getattr(self, "_miss_reason", "?"))
                self.summary["message"] = "提交按钮未就绪，稍后重试"
                self._retry_soon = True
            return
        if outcome is BookingOutcome.UNKNOWN:
            self.logger.warning("%s 提交结果不确定，停止自动重试，请到平台核对订单", name)
            self.summary["message"] = f"{name} 提交结果不确定，请核对订单"
            self._notify_failure(f"{name} 提交结果不确定，请到平台核对订单")

    def _booked_list(self) -> List[Dict[str, object]]:
        booked = getattr(self, "_booked", None)
        if booked is None:
            booked = self._booked = []
        return booked

    def _book_one(
        self, segments: List[Tuple[str, List[str]]], partners: List[str]
    ) -> Optional[BookingOutcome]:
        """一次完整下单：选场 → 提交 → 现场解验证码 → 带 token 提交。

        返回 None 表示「验证码没解开，什么都没提交」，可以安全地等下一轮；
        返回 UNKNOWN 表示「可能已经提交出去了」，必须停下来人工核对。
        """
        flow = self._captcha_flow()
        if flow is None:
            # 识别组件不可用：book_court 只支持单场地，退化成只订第一段
            court, page_slots = segments[0]
            return self._book_candidate(court, page_slots, partners, confirm_terms=False)
        return self._book_with_onsite_captcha(flow, segments, partners)

    def _book_with_onsite_captcha(
        self,
        flow,
        segments: List[Tuple[str, List[str]]],
        partners: List[str],
    ) -> Optional[BookingOutcome]:
        try:
            selected = self.booking_manager.select_segments(segments)
        except Exception as error:  # noqa: BLE001
            self.logger.warning("选场异常: %s", error)
            return BookingOutcome.UNKNOWN
        status = (selected or {}).get("status")
        if status == "error":
            return BookingOutcome.UNKNOWN
        if status not in ("selected", "partial"):
            return BookingOutcome.UNAVAILABLE
        if status == "partial" and not bool(self.config.get("booking.allow_partial", False)):
            return BookingOutcome.UNAVAILABLE
        if partners:
            self.booking_manager.click_partner(partners)

        # 走平台自己的提交流程：点提交（自动勾选预约须知）→ 验证码出来再现场解。
        # 不拦截 verifySuccess，让前端自己校验并 submitData，避免 token 语义对不上。
        timeout = float(self.config.get("runtime.submit_timeout_ms", 6000) or 6000) / 1000
        mark = flow.order_mark()
        if not flow.click_submit_button():
            flow.discard()
            self._miss_reason = "submit"
            return None

        # 有的场次不弹验证码，点完就直接提交
        response = flow.wait_order_response(mark, timeout=1.0)
        if not isinstance(response, dict) and flow.has_captcha(timeout=3.0):
            if not flow.solve_submit_captcha(max_tries=self._captcha_tries()):
                self._close_captcha()
                self._miss_reason = "captcha"
                self.logger.warning("验证码现场没解开，本轮不提交")
                return None
            # 点完坐标就等平台回执：3 秒还没反应、验证码面板又还开着，
            # 说明这次没点对（平台会换一张图），不要再点第二下，等下一轮。
            clicked_at = time.monotonic()
            response = None
            deadline = clicked_at + timeout + 2
            while time.monotonic() < deadline:
                response = flow.order_response(mark)
                if response:
                    break
                if time.monotonic() - clicked_at >= 3.0 and flow.has_captcha():
                    self._close_captcha()
                    self._miss_reason = "captcha"
                    self.logger.warning("验证码没通过（面板还开着），本轮放弃，稍后重试")
                    return None
                time.sleep(0.2)
            if isinstance(response, dict):
                self._on_captcha_ok()

        if isinstance(response, dict):
            code = str(response.get("code"))
            data = response.get("data")
            message = str(response.get("msg") or "")
            if code == "200" and data not in (None, "", {}, []):
                self.logger.info("平台接口回执：下单成功（%s）", str(data)[:60])
                return BookingOutcome.CREATED
            if code and code != "200":
                self.logger.warning("平台拒绝下单（code=%s）：%s", code, message)
                if "未支付" in message or "重复" in message:
                    # 已经有一张未支付订单，继续试别的场次也没用
                    return BookingOutcome.UNKNOWN
                return BookingOutcome.REJECTED
            if message:
                self.logger.info("平台接口回执：%s", message)

        result = flow.wait_outcome(timeout)
        return {
            "created": BookingOutcome.CREATED,
            "rejected": BookingOutcome.REJECTED,
        }.get(result, BookingOutcome.UNKNOWN)

    def _close_captcha(self):
        """解失败时把验证码弹窗关掉，别让它挡住下一轮的读表和选场。"""
        try:
            self.query_manager.click_close_button()
        except Exception:  # noqa: BLE001
            pass

    def _captcha_tries(self) -> int:
        """现场识别最多换几张图。只重试「没认出来」，点过没通过不再点。

        至少给 1 次换图机会：认不出时换图不计平台失败次数，
        但被抢的空场不会等我们到下一轮。
        """
        try:
            configured = int(self.config.get("captcha.max_attempts", 1) or 1)
        except (TypeError, ValueError):
            configured = 1
        return max(2, min(configured, 5))

    def _captcha_locked(self) -> bool:
        """平台提示验证码失败次数过多/锁定，就别继续试了。"""
        driver = getattr(getattr(self, "browser", None), "driver", None)
        if driver is None:
            return False
        try:
            text = str(driver.execute_script(
                "return (document.body && document.body.innerText || '').slice(-600);"
            ) or "")
        except Exception:  # noqa: BLE001
            return False
        return any(hint in text for hint in ("失败次数过多", "验证码错误次数", "已被锁定"))

    def _on_captcha_miss(self, name: str, slots: List[str]):
        """现场解失败：本轮放弃，连续失败就停下来，避免把账号送进风控。"""
        self._captcha_failures = getattr(self, "_captcha_failures", 0) + 1
        locked = self._captcha_locked()
        limit = 3
        self._retry_soon = True
        self.summary["message"] = f"验证码未通过，{CAPTCHA_RETRY_SECONDS} 秒后重试"
        self.logger.warning(
            "验证码未通过（第 %s 次，%s），本轮放弃%s",
            self._captcha_failures, name,
            "；平台提示失败次数过多" if locked else "",
        )
        if locked or self._captcha_failures >= limit:
            self._halt = True
            self.summary["message"] = (
                "验证码连续失败，已停止轮询；请稍后手动打开预约页重试"
                if not locked else "平台提示验证码失败次数过多，已停止轮询"
            )
            self.logger.error("%s", self.summary["message"])
            self._notify_failure(self.summary["message"])

    def _on_captcha_ok(self):
        self._captcha_failures = 0

    # ------------------------------------------------------------- 通知

    def _notify_failure(self, message: str):
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        if not self.config.get("feishu.notify_failure", False):
            return
        notifier.send_failure(message)

    def _send_availability(self, day, window):
        """只在「有连续 N 小时空场」时提醒；抢到/支付的消息另发。"""
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        if not window or not self.config.get("feishu.notify_availability", True):
            return
        courts: Dict[str, List[str]] = {}
        for court, slot, _ in window:
            courts.setdefault(court, []).append(slot)
        # 同一个日期的空场没变化就不再推送，否则每 60 秒刷一条飞书
        seen = getattr(self, "_seen", None)
        if seen is None:
            seen = self._seen = {}
        signature = tuple(sorted((court, tuple(slots)) for court, slots in courts.items()))
        if seen.get(day) == signature:
            self.logger.debug("%s 的空场和上次一致，跳过重复通知", day)
            return
        seen[day] = signature
        notifier.send_availability(day, courts)

    def _clear_seen(self):
        seen = getattr(self, "_seen", None)
        if isinstance(seen, dict):
            seen.clear()

    def _forget(self, day):
        seen = getattr(self, "_seen", None)
        if isinstance(seen, dict):
            seen.pop(day, None)

    # ------------------------------------------------------------- 循环

    def _wait_for_next_scan(self, seconds: int):
        self._stopped.wait(max(0, int(seconds)))

    def _next_wait(self, read_ok: bool) -> int:
        """读表正常就按设定间隔；连续失败就指数退避，并在第 3/6 次尝试恢复会话。"""
        base = max(5, int(getattr(self, "scan_interval", 60) or 60))
        if read_ok:
            self._failures = 0
            if getattr(self, "_retry_soon", False):
                # 验证码没解开：空场可能还在，隔几秒就再扫一次，别等满一个间隔
                self._retry_soon = False
                return min(CAPTCHA_RETRY_SECONDS, base)
            return base
        self._failures = getattr(self, "_failures", 0) + 1
        if self._failures in (3, 6) and self._recover():
            self._failures = 0
            return base
        return min(base * (2 ** min(self._failures - 1, 6)), 600)

    def _recover(self) -> bool:
        self.logger.warning("连续 %s 轮没读到预约表，尝试重新登录并恢复会话", self._failures)
        try:
            if self.start_pipeline():
                self.logger.info("会话已恢复，继续轮询")
                return True
        except Exception as error:  # noqa: BLE001
            self.logger.warning("恢复会话失败: %s", error)
        try:
            self.cleanup()
        except Exception as error:  # noqa: BLE001
            self.logger.debug("关闭旧浏览器失败: %s", error)
        return False

    # ------------------------------------------------------------- 支付等待

    @staticmethod
    def _booking_entries(booking: Dict[str, object]) -> List[Dict[str, object]]:
        """兼容两种结构：单场地 {court, slots} 与跨场地 {segments: [...]}。"""
        segments = booking.get("segments")
        if segments:
            return [
                {"court": str(item.get("court") or ""), "slots": list(item.get("slots") or [])}
                for item in segments
            ]
        return [{
            "court": str(booking.get("court") or ""),
            "slots": list(booking.get("slots") or []),
        }]

    @classmethod
    def _describe_booking(cls, booking: Dict[str, object]) -> str:
        return "；".join(
            f"{entry['court']} {'、'.join(entry['slots'])}"
            for entry in cls._booking_entries(booking)
        )

    def _entry_state(self, day: str, entry: Dict[str, object]) -> str:
        court = str(entry.get("court") or "")
        statuses: List[Optional[int]] = []
        for slot in entry.get("slots") or []:
            try:
                statuses.append(self.query_manager.slot_status(day, court, str(slot)))
            except Exception as error:  # noqa: BLE001
                self.logger.debug("查询支付状态失败: %s", error)
                return "unknown"
        if not statuses or any(not isinstance(item, int) for item in statuses):
            return "unknown"
        if any(item == 3 for item in statuses):
            return "unpaid"
        if all(item == 4 for item in statuses):
            return "paid"
        if all(item in (1, 2) for item in statuses):
            return "expired"
        return "unknown"

    def _payment_state(self, booking: Dict[str, object]) -> str:
        """paid / unpaid / expired / unknown。

        平台 day/info 里：3 = 未支付，4 = 已预约（已支付），1/2 = 订单已释放。
        跨场地会有多段，任一段没付都算未支付。
        """
        day = str(booking.get("day") or "")
        states = [self._entry_state(day, entry) for entry in self._booking_entries(booking)]
        if any(state == "unpaid" for state in states):
            return "unpaid"
        if states and all(state == "paid" for state in states):
            return "paid"
        if states and all(state == "expired" for state in states):
            return "expired"
        return "unknown"

    def _await_payment(self, booking: Dict[str, object]) -> str:
        """守着订单：paid → 结束；expired → 重新抢；stopped → 用户手动停。"""
        day = booking.get("day")
        description = self._describe_booking(booking)
        window = float(getattr(self, "payment_window_seconds", PAYMENT_WINDOW_MINUTES * 60))
        check = float(getattr(self, "payment_check_seconds", PAYMENT_CHECK_SECONDS))
        deadline = time.monotonic() + window
        started = time.monotonic()
        last_state = None
        while not self.stopped:
            state = self._payment_state(booking)
            if state != last_state:
                self.logger.info("订单状态：%s（%s %s）", state, day, description)
                last_state = state
            if state == "paid":
                self.logger.info("检测到订单已支付：%s %s", day, description)
                self._notify_paid(booking)
                return "paid"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "expired"
            if state == "expired" and time.monotonic() - started >= 30:
                # 平台数据可能有延迟：隔 3 秒再确认一次，别把还在的订单误判成失效
                if self._stopped.wait(3):
                    break
                if self._payment_state(booking) == "expired":
                    self.logger.warning("订单已被释放：%s %s", day, description)
                    return "expired"
                continue
            self.logger.info(
                "订单等待支付：%s %s，还剩约 %s 分钟",
                day, description, max(1, int(remaining // 60)),
            )
            self._notify_payment_reminder(booking, remaining)
            self.summary["message"] = (
                f"等待支付：{description}，还剩约 {max(1, int(remaining // 60))} 分钟"
            )
            if self._stopped.wait(min(check, max(0.05, remaining))):
                break
        return "stopped"

    def _notify_booking_success(self, booking: Dict[str, object]):
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        if not self.config.get("feishu.notify_success", True):
            return
        day = str(booking.get("day") or "")
        for entry in self._booking_entries(booking):
            notifier.send_booking_success(
                day,
                str(entry.get("court") or ""),
                list(entry.get("slots") or []),
                [self.partner_name] if self.partner_name else [],
                pay_within_minutes=PAYMENT_WINDOW_MINUTES,
            )

    def _notify_payment_reminder(self, booking: Dict[str, object], remaining: float):
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        minutes = max(1, int(remaining // 60))
        notifier.send_text(
            f"⏰ 还没有收到支付：{booking.get('day')} {self._describe_booking(booking)}\n"
            f"还剩约 {minutes} 分钟，超时订单会失效并重新抢占。"
        )

    def _notify_paid(self, booking: Dict[str, object]):
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        notifier.send_text(
            f"✅ 已支付：{booking.get('day')} {self._describe_booking(booking)}，轮询任务结束。"
        )

    def run(self) -> bool:
        if not self._validate_config():
            return False
        if not self._dates():
            self.logger.warning("所选轮询日期均已过去，请重新选择")
            self.summary.update(state="failed", message="请重新选择轮询日期")
            return False
        if not self.start_pipeline():
            self.summary.update(state="failed", message="登录或进入场馆失败")
            self.cleanup()
            return False
        self.is_running = True
        self.summary.update(state="polling", started_at=datetime.now().isoformat(timespec="seconds"))
        self.logger.info("开始轮询：%s，间隔 %ss", ", ".join(self._dates()), self.scan_interval)
        failed = False
        try:
            count = 0
            while not self.stopped and self._dates():
                count += 1
                self.logger.info("第 %s 轮", count)
                started = time.monotonic()
                try:
                    read_ok = self._perform_scan()
                except Exception as error:  # noqa: BLE001
                    self.logger.error("本轮扫描异常: %s", error, exc_info=True)
                    self.summary["message"] = str(error)
                    read_ok = False
                self.summary["rounds"] = count
                self.summary["last_seconds"] = round(time.monotonic() - started, 2)
                if self._booked_list() or getattr(self, "_halt", False):
                    break
                wait = self._next_wait(read_ok)
                self.summary["next_scan_at"] = (
                    datetime.now(BEIJING) + timedelta(seconds=wait)
                ).isoformat(timespec="seconds")
                self.logger.info("%s 秒后开始下一轮", wait)
                self._wait_for_next_scan(wait)
        except Exception as error:  # noqa: BLE001
            self.logger.error("轮询中断: %s", error, exc_info=True)
            self.summary.update(state="failed", message=str(error))
            failed = True
        finally:
            self.is_running = False
            self.cleanup()
        if getattr(self, "_halt", False):
            failed = True
        if getattr(self, "_paid", False):
            self.summary.update(state="done", message=self.summary.get("message") or "已支付，轮询结束")
        else:
            self.summary.update(state="failed" if failed else "stopped")
        return not failed

    def stop(self):
        super().stop()
        self.is_running = False
