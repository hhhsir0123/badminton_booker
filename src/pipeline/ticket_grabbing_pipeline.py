"""07:00 单次放场任务。

时间线（北京时间）：

    T-advance_time  启动浏览器、复用 Cookie 登录、进入场馆页（默认提前 120 秒）
    T-30s           用 HTTP Date 估算本机与服务器的时钟偏差
    T-1.5s          开始探测 day/info 接口，放场瞬间立刻刷新并读表
    T+0 ~ T+4s      读表 → 选场 → 提交；失败按候选顺序再试，最多 max_attempts 次

为什么这样比「到点刷新 + sleep」快：
- 登录、进馆、注入脚本全部提前完成，07:00:00 那一刻只做「取数据 + 点格子」；
- 页面内探测一次 20~50ms，不用 WebDriver 往返；
- 从读到可订格到点击提交只有一次 JS 调用，没有固定 sleep。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

from ..core.booking_result import BookingOutcome
from ..core.court_query import parse_slot
from ..core.page_js import SERVER_TIME_JS
from .base_pipeline import BasePipeline

BEIJING = timezone(timedelta(hours=8))


# --------------------------------------------------------------------- 纯逻辑
def booking_plan(config, now: datetime, preview: bool = False) -> Tuple[datetime, date]:
    """返回（开售时刻, 入场日）。

    - 正式任务：如果今天的开售时间已经过去（超过 30 秒宽限），自动顺延到明天；
    - 预览：不滚动，直接看「按当前配置本应抢的那一天」现在能不能订。
    """
    now = now.astimezone(BEIJING)
    ahead = config.get("booking.booking_days_ahead", 2)
    if type(ahead) is not int or ahead < 0:
        raise ValueError("booking.booking_days_ahead 应为非负整数")

    release_clock = datetime.strptime(
        str(config.get("booking.target_time_in_day", "07:00:00")), "%H:%M:%S"
    ).time()

    fixed = config.get("booking.booking_date")
    if fixed not in (None, ""):
        play_day = date.fromisoformat(str(fixed))
        if play_day < now.date():
            raise ValueError(f"指定的入场日 {play_day} 已经过去")
        opening = datetime.combine(play_day - timedelta(days=ahead), release_clock, BEIJING)
        return opening, play_day

    opening = datetime.combine(now.date(), release_clock, BEIJING)
    if not preview and now > opening + timedelta(seconds=30):
        opening += timedelta(days=1)
    return opening, opening.date() + timedelta(days=ahead)


def _clock(value) -> str:
    if not isinstance(value, str):
        raise ValueError("时段使用 HH:MM 格式，请在 YAML 里给时间加引号")
    return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")


def preferences(config) -> List[Tuple[Optional[str], Optional[Tuple[str, str]], Tuple[Tuple[str, str], ...]]]:
    """把配置展开成候选列表：[(指定场地 或 None, (开始,结束) 或 None, 组合时段)]。"""
    explicit = getattr(config, "explicit", set())
    use_legacy = "booking.preferred_time_slots" in explicit and "booking.preferences" not in explicit

    if use_legacy:
        slots = config.get("booking.preferred_time_slots", [])
        if not isinstance(slots, list) or not slots:
            raise ValueError("booking.preferred_time_slots 需要至少一个时段")
        grouped = tuple(dict.fromkeys(parse_slot(str(slot)) for slot in slots))
        if not all(grouped):
            raise ValueError("旧格式时段需要 HH:MM-HH:MM")
        return [(None, None, grouped)]

    items = config.get("booking.preferences")
    if not isinstance(items, list) or not items:
        raise ValueError("booking.preferences 至少需要一条候选")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("每条候选都需要 court / start / end")
        court = str(item.get("court") or "").strip()
        if not court:
            raise ValueError("候选缺少场地；想要任意场地请填「任意」")
        start, end = _clock(item.get("start")), _clock(item.get("end"))
        if start >= end:
            raise ValueError(f"候选 {court} 的结束时间应晚于开始时间")
        result.append((None if court in ("任意", "*") else court, (start, end), ()))
    return result


def candidates(
    availability: Dict[str, Dict[str, str]],
    choices,
    cutoff: Optional[str] = None,
    fallback_any: bool = False,
) -> List[Tuple[str, List[str]]]:
    """把配置优先级映射成实际可点的场格，返回 [(场地, [页面时段原文])]。

    fallback_any=True 时，如果一条候选都不满足，就退而求其次：
    每个场地取最早的一段可用时段，至少保证「不空手」。
    """
    courts: Dict[str, Dict[str, str]] = {}
    for court, slots in availability.items():
        kept = {
            key: label for key, label in slots.items()
            if cutoff is None or key.split("-")[0] > cutoff
        }
        if kept:
            courts[court] = kept

    seen = set()
    ordered: List[Tuple[str, List[str]]] = []
    for preferred, span, grouped in choices:
        for court, kept in courts.items():
            if preferred is not None and preferred != court:
                continue
            if grouped:
                if not all(key in kept for key in grouped):
                    continue
                slots = [kept[key] for key in grouped]
            else:
                start, end = span
                pairs = {tuple(key.split("-")): label for key, label in kept.items()}
                # 从终点倒推，找一条首尾相接、无缺口的时段链
                paths: Dict[str, List[str]] = {end: []}
                for left, right in sorted(pairs, reverse=True):
                    if left >= start and right in paths:
                        paths[left] = [pairs[(left, right)], *paths[right]]
                if start not in paths:
                    continue
                slots = paths[start]
            key = (court, tuple(slots))
            if key not in seen:
                seen.add(key)
                ordered.append((court, slots))

    if not ordered and fallback_any:
        for court, kept in courts.items():
            for slot_key in sorted(kept):
                entry = (court, [kept[slot_key]])
                if entry not in ordered:
                    ordered.append(entry)
                break
    return ordered


def released(probe: Dict, baseline: Optional[int]) -> bool:
    """day/info 的体积比放场前明显变化，就认为放场了。"""
    if probe.get("status") != 200 or baseline is None:
        return False
    length = int(probe.get("length") or 0)
    if length < 400:
        return False
    return abs(length - baseline) > max(80, int(baseline * 0.05))


# --------------------------------------------------------------------- 流程
class TicketGrabbingPipeline(BasePipeline):
    def __init__(self, config_path: Optional[str] = None, *, now=None, overrides: Optional[Dict[str, object]] = None):
        super().__init__(config_path)
        for key, value in (overrides or {}).items():
            self.config.set(key, value)
        self._now = now or (lambda: datetime.now(BEIJING))
        self.last_outcome: Optional[BookingOutcome] = None
        self.summary = {"state": "idle"}

    # ------------------------------------------------------------- 小工具

    def _note(self, **fields):
        self.summary.update(fields)
        self.summary["updated_at"] = self._now().astimezone(BEIJING).isoformat(timespec="milliseconds")

    def _sleep(self, seconds: float) -> bool:
        """可被 stop() 打断的 sleep，返回 False 表示被取消。"""
        if seconds <= 0:
            return not self.stopped
        return not self._stopped.wait(seconds)

    def _wait_until(self, deadline: datetime) -> bool:
        while not self.stopped:
            remaining = (deadline - self._now()).total_seconds()
            if remaining <= 0:
                return True
            if not self._sleep(min(remaining, 30)):
                return False
        return False

    def _prepare(self, deadline: datetime) -> bool:
        """登录 + 进馆；失败就重试到 deadline（临场网络抖动很常见，不能一次失败就放弃）。"""
        attempt = 0
        while not self.stopped:
            attempt += 1
            if self.start_pipeline():
                if attempt > 1:
                    self.logger.info("第 %s 次准备成功", attempt)
                return True
            self.logger.warning("准备会话失败（第 %s 次）", attempt)
            if attempt >= 5 or self._now() >= deadline:
                return False
            self.cleanup()
            self.logger.info("15 秒后重试登录 / 进馆")
            if not self._sleep(15):
                return False
        return False

    def _prewarm(self, play_day: date) -> None:
        """提前把目标日期渲染出来：07:00 只需要刷新数据，省掉首次渲染的几百毫秒。"""
        if self.stopped:
            return
        try:
            if self.query_manager.focus_date(play_day.isoformat(), fresh=False):
                self.logger.info("预热：%s 已显示在预约表上，放场时只需要刷新数据", play_day)
            else:
                self.logger.info("预热：%s 还不在可预约窗口内，放场后再取", play_day)
        except Exception as error:  # noqa: BLE001
            self.logger.debug("预热失败（不影响主流程）: %s", error)

    def _clear_captcha(self) -> bool:
        """页面有验证码就解决掉；没有的话只花一次 ~5ms 的检查。"""
        solver = getattr(self, "captcha_solver", None)
        if solver is None:
            return True
        started = time.monotonic()
        try:
            ok = solver.solve()
        except Exception as error:  # noqa: BLE001
            self.logger.warning("验证码处理异常: %s", error)
            return False
        elapsed = int((time.monotonic() - started) * 1000)
        if not ok:
            self.logger.warning("验证码未通过（%sms）", elapsed)
        elif elapsed > 50:
            self.logger.info("验证码已处理（%sms）", elapsed)
        return ok

    def _pre_solve_captcha(self) -> None:
        """开售前先把下单验证码解掉，拿到 captchaVerification 留着 07:00 用。

        真机验证（2026-11-19）：token 解出来后 10 秒仍可正常下单，
        服务端有效期 180 秒，所以在 T-60s 预解、T0 直接提交是可行的。
        """
        flow = getattr(self, "submit_captcha", None)
        if flow is not None and self.config.get("captcha.pre_solve", True):
            try:
                captured = flow.pre_solve()
            except Exception as error:  # noqa: BLE001
                self.logger.warning("预解下单验证码异常: %s", error)
                captured = None
            if captured:
                self.logger.info("下单验证码已预解，token=%s（有效期 %.0f 秒）",
                                 captured.token[:8], flow.ttl_seconds)
                return
            self.logger.info("下单验证码没预解成功，07:00 走现场识别")

        solver = getattr(self, "captcha_solver", None)
        if solver is None or not self.config.get("captcha.pre_solve", True):
            return
        try:
            if not solver.present():
                self.logger.info("开售前页面上没有验证码（大概率是提交时才出现）")
                return
            started = time.monotonic()
            ok = solver.solve()
            elapsed = int((time.monotonic() - started) * 1000)
            self.logger.info(
                "开售前验证码%s（%sms），放场时看它是否还会出现",
                "已通过" if ok else "未通过",
                elapsed,
            )
        except Exception as error:  # noqa: BLE001
            self.logger.debug("开售前处理验证码失败（不影响主流程）: %s", error)

    def _clock_offset(self) -> float:
        """估算（服务器时间 - 本机时间），秒。HTTP Date 只有秒精度，所以只纠正明显偏差。"""
        manual = self.config.get("runtime.clock_offset_ms", 0)
        if manual:
            return float(manual) / 1000.0
        samples = []
        for _ in range(3):
            try:
                before = time.time()
                payload = self.browser.driver.execute_async_script(SERVER_TIME_JS) or {}
                after = time.time()
            except Exception:  # noqa: BLE001
                break
            stamp = payload.get("date")
            if not stamp:
                continue
            try:
                server_epoch = parsedate_to_datetime(stamp).timestamp()
            except (TypeError, ValueError):
                continue
            samples.append(server_epoch - (before + after) / 2)
        if not samples:
            return 0.0
        samples.sort()
        offset = samples[len(samples) // 2]
        if abs(offset) < 0.8 or abs(offset) > 30:
            return 0.0
        self.logger.warning("本机与服务器时钟相差 %.1f 秒，已自动校正", offset)
        return offset

    # ------------------------------------------------------------- 读表

    def _read_availability(self, play_day: str, refresh: Optional[str] = None) -> Dict[str, Dict[str, str]]:
        refresh = refresh or str(self.config.get("runtime.grid_refresh", "auto"))
        if not self.query_manager.focus_date(play_day, fresh=True, refresh=refresh):
            return {}
        merged: Dict[str, Dict[str, str]] = {}
        for page in self.query_manager.iter_pages():
            for court, slots in self.query_manager.free_slots(page).items():
                merged.setdefault(court, {}).update(slots)
        return merged

    def _candidates_for(self, play_day: str, now: datetime, choices) -> List[Tuple[str, List[str]]]:
        """优先只读当前页；当前页没有候选才翻页。热路径上省掉翻页开销。"""
        if not self._clear_captcha():
            return []
        refresh = str(self.config.get("runtime.grid_refresh", "auto"))
        if not self.query_manager.focus_date(play_day, fresh=True, refresh=refresh):
            return []
        cutoff = now.strftime("%H:%M") if play_day == now.date().isoformat() else None
        fallback = bool(self.config.get("booking.fallback_any", False))
        page = self.query_manager.snapshot()
        if page:
            found = candidates(self.query_manager.free_slots(page), choices, cutoff, fallback)
            if found:
                return found
        for page in self.query_manager.iter_pages():
            found = candidates(self.query_manager.free_slots(page), choices, cutoff, fallback)
            if found:
                return found
        return []

    # ------------------------------------------------------------- 放场窗口

    def _grab(self, play_day: str, opening: datetime, choices) -> List[Tuple[str, List[str]]]:
        lead = float(self.config.get("runtime.release_probe_lead_ms", 1500)) / 1000
        interval = max(0.05, float(self.config.get("runtime.release_probe_interval_ms", 120)) / 1000)
        grace = float(self.config.get("runtime.release_grace_ms", 4000)) / 1000
        wake_at = opening - timedelta(seconds=lead)
        deadline = opening + timedelta(seconds=grace)

        if not self._wait_until(wake_at):
            return []

        baseline = None
        backoff = 0.0
        while not self.stopped and self._now() < deadline:
            now = self._now()
            probe = self.query_manager.probe(play_day)
            if probe.get("status") == 200:
                if baseline is None and now < opening:
                    baseline = int(probe.get("length") or 0)
                    self.logger.info("放场前 day/info 体积 %s 字节", baseline)
                elif released(probe, baseline):
                    self.logger.info(
                        "探测到放场：%s 字节（基线 %s，%sms）",
                        probe.get("length"), baseline, int(probe.get("ms") or 0),
                    )

            if now >= opening or released(probe, baseline):
                self._note(state="reading")
                found = self._candidates_for(play_day, now, choices)
                if found:
                    return found
                # 还没放场或者已经被抢完：退避重试，避免请求风暴
                backoff = min(max(backoff * 1.5, 0.4), 1.6)
                if not self._sleep(backoff):
                    return []
                continue

            if not self._sleep(interval):
                return []
        return []

    # ------------------------------------------------------------- 主流程

    def run(self, preview: bool = False, dry_run: bool = False) -> bool:
        try:
            return self._run(preview, dry_run)
        except Exception as error:  # noqa: BLE001 - 任务级异常也要通知，不能让调度静默失败
            self.logger.error("任务异常: %s", error, exc_info=True)
            self._note(state="failed", message=f"任务异常: {error}")
            self._notify_failure(f"任务异常：{error}")
            return False
        finally:
            self.summary["updated_at"] = self._now().astimezone(BEIJING).isoformat(timespec="seconds")

    def _run(self, preview: bool, dry_run: bool = False) -> bool:
        now = self._now()
        opening, play_day = booking_plan(self.config, now, preview or dry_run)
        choices = preferences(self.config)
        advance = int(self.config.get("booking.advance_time", 120))
        max_attempts = int(self.config.get("booking.max_attempts", 3))
        allow_partial = bool(self.config.get("booking.allow_partial", False))
        partner = str(self.config.get("user.partner_name") or "").strip()
        partners = [partner] if partner else []

        mode = "preview" if preview else ("dry_run" if dry_run else "book")
        self._note(
            state="planning", mode=mode, opening=opening.isoformat(timespec="seconds"),
            play_day=play_day.isoformat(), outcome=None, message="",
            candidates=[], timings={},
        )
        self.logger.info("北京时间开售 %s，入场日 %s，模式 %s", opening.isoformat(" "), play_day, mode)

        if preview or dry_run:
            if not self.start_pipeline():
                self._note(state="failed", message="登录或进入场馆失败")
                return False
            try:
                found = self._candidates_for(play_day.isoformat(), self._now(), choices)
                self._note(
                    state="preview",
                    candidates=[{"court": court, "slots": slots} for court, slots in found],
                    message=f"{play_day} 共 {len(found)} 个可订候选",
                )
                if dry_run:
                    return self._rehearse(found)
                return bool(found)
            finally:
                self.cleanup()

        self._note(state="waiting", message=f"等待 {opening:%H:%M:%S} 开售")
        if not self._wait_until(opening - timedelta(seconds=advance)):
            self._note(state="cancelled", message="任务在开始前被取消")
            return False

        self._note(state="waking")
        started = time.monotonic()
        if not self._prepare(opening - timedelta(seconds=10)):
            self._note(state="failed", message="登录或进入场馆失败，已重试到放场前")
            self._notify_failure("登录或进入场馆失败，本次没有抢票")
            return False
        self._note(timings={"session_ready_ms": int((time.monotonic() - started) * 1000)})
        self._prewarm(play_day)
        self._pre_solve_captcha()

        offset = self._clock_offset()
        target = opening - timedelta(seconds=offset)
        if not self._wait_until(target - timedelta(milliseconds=1500)):
            self._note(state="cancelled", message="任务在放场前被取消")
            return False

        self._note(state="grabbing")
        found = self._grab(play_day.isoformat(), target, choices)
        if not found:
            self._note(state="missed", message="放场窗口内没有读到可订场次")
            self.logger.warning("入场日 %s 在放场窗口内没有可订场次", play_day)
            self._notify_failure(f"{play_day} 没有读到可订场次（放场窗口内）")
            return False

        self._note(candidates=[{"court": c, "slots": s} for c, s in found])
        for index, (court, slots) in enumerate(found[:max_attempts], start=1):
            if self.stopped:
                self._note(state="cancelled", message="提交前被取消")
                return False
            self.logger.info("候选 %s/%s：%s %s", index, min(len(found), max_attempts), court, ", ".join(slots))
            started = time.monotonic()
            if index == 1 and getattr(self, "submit_captcha", None) and self.submit_captcha.presolved:
                if self._submit_presolved(court, slots):
                    elapsed = int((time.monotonic() - started) * 1000)
                    self.summary.setdefault("timings", {})[f"attempt_{index}_ms"] = elapsed
                    self.summary["presolved_used"] = True
                    self._note(state="done", outcome="created", message="平台已建单（预解验证码），请及时付款")
                    self.logger.info("预订成功（预解）：%s %s（%sms）", court, ", ".join(slots), elapsed)
                    self._notify(play_day, court, slots)
                    keep_open = int(self.config.get("runtime.keep_open_after_success", 600) or 0)
                    if keep_open > 0:
                        self.summary["keep_open_seconds"] = keep_open
                        self._sleep(keep_open)
                    return True
                self.logger.info("预解 token 提交未成功，回落到现场识别")
            outcome = self.booking_manager.book_court(
                court_name=court,
                time_slots=slots,
                partner_names=partners,
                is_click_parter=bool(partners),
                submit_timeout_ms=int(self.config.get("runtime.submit_timeout_ms", 6000)),
                allow_partial=allow_partial,
            )
            elapsed = int((time.monotonic() - started) * 1000)
            self.last_outcome = outcome
            self.summary.setdefault("timings", {})[f"attempt_{index}_ms"] = elapsed

            if outcome is BookingOutcome.CREATED:
                self._note(state="done", outcome="created", message="平台已建单，请及时付款")
                self.logger.info("预订成功：%s %s（%sms）", court, ", ".join(slots), elapsed)
                self._notify(play_day, court, slots)
                # 订单要在同一个浏览器窗口里付款，别急着 quit
                keep_open = int(self.config.get("runtime.keep_open_after_success", 600) or 0)
                if keep_open > 0:
                    self.summary["keep_open_seconds"] = keep_open
                    self.logger.info("浏览器保持打开 %s 秒，请完成支付（停止任务可提前关闭）", keep_open)
                    self._sleep(keep_open)
                return True
            if outcome is BookingOutcome.UNAVAILABLE:
                self.logger.info("场格已被抢走，尝试下一个候选")
                continue
            if outcome is BookingOutcome.REJECTED:
                self.logger.warning("平台拒绝本次提交，尝试下一个候选")
                continue
            self._note(state="unknown", outcome="unknown", message="提交结果不确定，请到浏览器核对订单")
            self.logger.error("提交结果不确定（%sms），已停止自动重试", elapsed)
            self._notify_failure(f"{court} {'、'.join(slots)} 提交结果不确定，请核对订单")
            return False

        self._note(state="failed", outcome="unavailable", message="所有候选都被抢完了")
        self._notify_failure(f"{play_day} 的候选在提交时都被抢完了")
        return False

    def _submit_presolved(self, court: str, slots: List[str]) -> bool:
        """选场 → 点提交 → 用预解 token 直接下单（不现场解验证码）。"""
        try:
            selected = self.booking_manager.select_slots(court, slots)
        except Exception as error:  # noqa: BLE001
            self.logger.warning("预解路径选场异常: %s", error)
            return False
        if selected.get("status") not in ("selected", "partial"):
            self.logger.info("预解路径没选中场格: %s", selected)
            return False
        started = time.monotonic()
        if not self.submit_captcha.submit_selected():
            self.logger.warning("预解 token 提交失败")
            return False
        timeout = float(self.config.get("runtime.submit_timeout_ms", 6000)) / 1000
        ok = self.submit_captcha.wait_success(timeout=timeout)
        self.summary.setdefault("timings", {})["presolved_submit_ms"] = int((time.monotonic() - started) * 1000)
        return ok

    def _rehearse(self, found: List[Tuple[str, List[str]]]) -> bool:
        """演练：真的去点场格，但绝不提交订单。用来验证选择器和选中逻辑。"""
        if not found:
            self._note(state="dry_run", message="没有可订候选，选场演练无法进行")
            self.logger.warning("没有可订候选，试试用 --date 指定一个当前有空的日期")
            return False

        court, slots = found[0]
        self.logger.info("演练：尝试选中 %s %s（不会提交订单）", court, ", ".join(slots))
        result = self.booking_manager.select_slots(court, slots)
        self._note(
            state="dry_run",
            selection={"court": court, "slots": slots, "status": result.get("status")},
            message=f"演练 {court} {', '.join(slots)} → {result.get('status')}（未提交订单）",
        )
        self.logger.info("演练结果: %s %s", result.get("status"), result.get("detail"))
        try:
            stamp = self._now().strftime("%Y%m%d_%H%M%S")
            path = f"logs/dry_run_{stamp}.png"
            self.browser.driver.save_screenshot(path)
            self.logger.info("演练截图: %s，请确认场格是否已经变成选中态", path)
        except Exception as error:  # noqa: BLE001
            self.logger.debug("保存演练截图失败: %s", error)
        return result.get("status") == "selected"

    def _notify(self, play_day: date, court: str, slots: List[str]):
        partners = [self.config.get("user.partner_name")] if self.config.get("user.partner_name") else []
        feishu = getattr(self, "feishu_notifier", None)
        if feishu and self.config.get("feishu.notify_success", True):
            feishu.send_booking_success(play_day.isoformat(), court, slots, partners)

    def _notify_failure(self, message: str):
        feishu = getattr(self, "feishu_notifier", None)
        if feishu and self.config.get("feishu.notify_failure", False):
            feishu.send_failure(message)

    # ------------------------------------------------------------- 预览接口

    def preview(self) -> Dict[str, object]:
        """立即查一次目标日期的可订场次，不下单。"""
        opening, play_day = booking_plan(self.config, self._now(), preview=True)
        choices = preferences(self.config)
        self.summary = {"state": "connecting", "mode": "preview",
                        "opening": opening.isoformat(timespec="seconds"),
                        "play_day": play_day.isoformat()}
        if not self.start_pipeline():
            self.summary.update(state="failed", message="登录或进入场馆失败")
            return self.summary
        try:
            found = self._candidates_for(play_day.isoformat(), self._now(), choices)
            self.summary.update(
                state="preview",
                candidates=[{"court": court, "slots": slots} for court, slots in found],
                message=f"{play_day} 共 {len(found)} 个可订候选",
            )
        finally:
            self.cleanup()
        return self.summary
