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

import threading
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
    """只有业务校验成功且响应包含目标日期，才确认日期数据已就绪。"""
    return (probe.get("status") == 200 and probe.get("ok") is True
            and probe.get("target_date_present") is True)


# --------------------------------------------------------------------- 流程
class TicketGrabbingPipeline(BasePipeline):
    def __init__(self, config_path: Optional[str] = None, *, now=None, overrides: Optional[Dict[str, object]] = None):
        super().__init__(config_path)
        for key, value in (overrides or {}).items():
            self.config.set(key, value)
        self._now = now or (lambda: datetime.now(BEIJING))
        self.last_outcome: Optional[BookingOutcome] = None
        self.submit_captcha = None
        self.summary = {"state": "idle"}

    def start(self) -> bool:
        if not super().start():
            return False
        # 抢票模式：格子不是 free 立即判死；点完只等 200ms 看 class
        if getattr(self, "booking_manager", None) is not None:
            self.booking_manager.cell_wait_ms = int(self.config.get("runtime.cell_wait_ms", 200) or 200)
        # 实验开关：是否尝试「不整页刷新，只改日期栏状态」（默认关）
        if getattr(self, "query_manager", None) is not None:
            self.query_manager.patch_date_bar = bool(self.config.get("runtime.patch_date_bar", False))
        if not self.config.get("captcha.pre_solve", True):
            self._note(captcha_pre_solve={"state": "disabled", "message": "预解已关闭"})
            return True
        if self.submit_captcha is not None:
            return True
        try:
            from ..core.submit_captcha import SubmitCaptchaFlow
            from ..utils.click_word_solver import ClickWordSolver

            provider = None
            source = str(self.config.get("captcha.provider", "local") or "local").lower()
            fallback = bool(self.config.get("captcha.fallback_to_local", True))
            if source == "ttshitu":
                from ..utils.ttshitu_provider import TTShituProvider

                provider = TTShituProvider.from_config(self.config)
                provider.check_credentials()
            elif source != "local":
                raise ValueError("不支持的验证码识别方式")
            solver = ClickWordSolver(provider=provider, fallback_to_local=fallback)
            if (provider is None or fallback) and not solver.warmup():
                raise RuntimeError("本地点选验证码模型未就绪")
            self.submit_captcha = SubmitCaptchaFlow(self.browser.driver, solver)
            self._note(captcha_pre_solve={"state": "component_ready", "message": "预解组件已就绪，尚未获取验证码"})
            self.logger.info("下单验证码预解组件已就绪（%s），开售前将获取并验证预解结果", source)
        except Exception as error:  # noqa: BLE001
            self._presolve_fallback("预解组件初始化失败（%s）" % type(error).__name__)
        return True

    def cleanup(self):
        flow = getattr(self, "submit_captcha", None)
        if flow is not None:
            flow.discard()
            self.submit_captcha = None
        super().cleanup()

    # ------------------------------------------------------------- 小工具

    def _note(self, **fields):
        timings = fields.pop("timings", None)
        if timings is not None:
            self.summary.setdefault("timings", {}).update(timings)
        state = fields.get("state")
        if state and state != self.summary.get("state"):
            stamp = time.monotonic()
            previous = getattr(self, "_stage_started", None)
            if previous is not None:
                previous_state, started = previous
                self.summary.setdefault("timings", {})[f"{previous_state}_ms"] = int((stamp - started) * 1000)
            self._stage_started = (state, stamp)
            self.logger.info("流程状态：%s → %s", self.summary.get("state", "未启动"), state)
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

    def _presolve_fallback(self, reason: str) -> None:
        flow = getattr(self, "submit_captcha", None)
        if flow is not None and not flow.discard():
            raise RuntimeError("无法恢复现场验证码处理，请重新启动任务")
        message = "预解未完成：%s；将改为提交时现场验证" % reason
        self._note(captcha_pre_solve={"state": "fallback", "message": message})
        self.logger.warning(message)

    def _validate_pre_solved(self, min_validity: float = 0) -> bool:
        if not self.config.get("captcha.pre_solve", True):
            return False
        flow = getattr(self, "submit_captcha", None)
        if flow is not None and flow.ready_for_submission(min_validity=min_validity):
            self._note(captcha_pre_solve={"state": "ready", "message": "预解结果已通过有效性检查"})
            return True
        self._presolve_fallback("没有有效预解结果，或结果已过期、页面已刷新")
        return False

    def _log_date_bar_state(self, tag: str):
        """记录日期栏状态（只读诊断）：几个页签、页面的 advanceReservationDays 是多少。

        用来判断「不放刷新能不能让新日期出现在日期栏」：
        如果放场前后 advanceReservationDays 变了、日期栏却还是旧的，就说明是页面缓存，
        值得做「改状态 + 重渲染」；否则只能刷新。
        """
        script = """
        try {
          const seen = new Set(); let vm = null;
          function find(node, d) {
            if (!node || d > 12 || seen.has(node)) return null;
            seen.add(node);
            if (node.$data && node.$data.tableVenueSite) return node;
            for (const c of (node.$children || [])) { const r = find(c, d + 1); if (r) return r; }
            return null;
          }
          for (const el of document.querySelectorAll('*')) {
            const v = el.__vue__; if (!v) continue;
            vm = find(v, 0); if (vm) break;
          }
          const tabs = [...document.querySelectorAll('.date_box > div')]
            .filter(e => e.getClientRects().length)
            .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim());
          return JSON.stringify({
            tabs: tabs,
            advance: vm ? vm.advanceReservationDays : null,
            dateList: vm ? vm.reservationDateList : null
          });
        } catch (e) { return JSON.stringify({error: String(e)}); }
        """
        try:
            driver = getattr(getattr(self, "browser", None), "driver", None)
            if driver is None:
                return
            self.logger.info("[指标] 日期栏(%s)=%s", tag, driver.execute_script(script))
        except Exception as error:  # noqa: BLE001
            self.logger.debug("日期栏诊断失败: %s", error)

    def _presolve_useful(self, day: str) -> bool:
        """放场要整页刷新时，预解 token 必被作废，不如省掉、提交时现场解。

        判据：T-60s 时目标日期在不在日期栏。D+2 在放场前永远不在，
        所以这一步实际会跳过预解；D+1 之类的场景仍然照旧预解。
        """
        try:
            if self.query_manager is None:
                return True
            if getattr(self.query_manager, "patch_date_bar", False):
                # 已启用「不整页刷新」：预解 token 不会被刷新销毁，值得预解
                self.logger.info("已启用日期栏 patch（不整页刷新），保留预解")
                return True
            if self.query_manager.index_of(day) is None:
                self.logger.info(
                    "目标日期 %s 还不在日期栏（放场会整页刷新），跳过预解、改用提交时现场解", day
                )
                return False
        except Exception as error:  # noqa: BLE001
            self.logger.debug("判断预解是否有用失败（按有用处理）: %s", error)
            return True
        return True

    def _pre_solve_captcha(self, min_validity: float = 0) -> bool:
        started = time.monotonic()
        try:
            return self._pre_solve_captcha_impl(min_validity)
        finally:
            elapsed = int((time.monotonic() - started) * 1000)
            self.summary.setdefault("timings", {})["presolve_ms"] = elapsed
            status = self.summary.get("captcha_pre_solve", {})
            self.logger.info("下单验证码预解结束：状态=%s，原因=%s，整体耗时=%sms",
                             status.get("state", "unknown"), status.get("message", ""), elapsed)

    def _pre_solve_captcha_impl(self, min_validity: float = 0) -> bool:
        """主动获取并预解下单验证码；配置开启与实际预解成功分别记录。"""
        if not self.config.get("captcha.pre_solve", True):
            self._note(captcha_pre_solve={"state": "disabled", "message": "预解已关闭"})
            return False
        flow = getattr(self, "submit_captcha", None)
        if flow is None:
            self._presolve_fallback("预解组件未就绪")
            return False
        self._note(captcha_pre_solve={"state": "solving", "message": "正在获取并预解下单验证码"})
        try:
            captured = flow.pre_solve(max_rounds=int(self.config.get("captcha.max_attempts", 1)))
        except Exception as error:  # noqa: BLE001
            self._presolve_fallback("预解发生异常（%s）" % type(error).__name__)
            return False
        if captured is None:
            self._presolve_fallback("未取得下单验证码验证结果")
            return False
        if not self._validate_pre_solved(min_validity=min_validity):
            return False
        self.logger.info("下单验证码已预解，验证结果完整且有效（剩余约 %.0f 秒）",
                         flow.ttl_seconds - captured.age())
        return True

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
        """完整读取后再筛偏好；读取失败不能解释成库存为空。"""
        started = time.monotonic()
        self.summary["availability_attempts"] = int(self.summary.get("availability_attempts", 0)) + 1
        result = {"date": play_day, "status": "query_failed", "error": None,
                  "pages": 0, "free_slots": 0, "candidates": 0}
        try:
            if not self._clear_captcha():
                result["error"] = "captcha-blocked"
                return []
            refresh = str(self.config.get("runtime.grid_refresh", "auto"))
            if not self.query_manager.focus_date(play_day, fresh=True, refresh=refresh):
                detail = getattr(self.query_manager, "last_query_status", {})
                result["error"] = detail.get("error") if isinstance(detail, dict) else "date-not-ready"
                result["error"] = result["error"] or "date-not-ready"
                return []
            cutoff = now.strftime("%H:%M") if play_day == now.date().isoformat() else None
            fallback = bool(self.config.get("booking.fallback_any", False))
            # 优先复用放场探测那次 day/info 的响应（同一份数据，不再多发一次请求）
            probe = getattr(self, "_released_probe", None) or {}
            probe_slots = probe.get("slots") if isinstance(probe, dict) else None
            api_merged = probe_slots if isinstance(probe_slots, dict) else self.query_manager.api_free_slots(play_day)
            if isinstance(api_merged, dict):
                merged: Dict[str, Dict[str, str]] = api_merged
                result["source"] = "api"
                result["pages"] = 0
                self.logger.info("[指标] 候选来源=%s，场格=%s，耗时=%sms",
                                 "探测响应" if isinstance(probe_slots, dict) else "接口",
                                 sum(len(slots) for slots in merged.values()),
                                 int((time.monotonic() - started) * 1000))
            else:
                result["source"] = "dom"
                merged = {}
                for page in self.query_manager.iter_pages():
                    result["pages"] += 1
                    for court, slots in self.query_manager.free_slots(page).items():
                        merged.setdefault(court, {}).update(slots)
                page_error = getattr(self.query_manager, "last_page_error", None)
                if result["pages"] == 0 or isinstance(page_error, str):
                    result["error"] = page_error if isinstance(page_error, str) else "table-missing"
                    return []
            self.summary["availability_reads"] = int(self.summary.get("availability_reads", 0)) + 1
            result["free_slots"] = sum(len(slots) for slots in merged.values())
            found = candidates(merged, choices, cutoff, fallback)
            result["candidates"] = len(found)
            result["status"] = "matched" if found else ("no_match" if merged else "no_inventory")
            return found
        except Exception as error:  # 页面跳转、DOM 读取和解析错误均属于查询失败
            result["error"] = type(error).__name__
            return []
        finally:
            result["ms"] = int((time.monotonic() - started) * 1000)
            self.summary["last_availability"] = result
            counts = self.summary.setdefault("availability_results", {})
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            self.logger.info("场次读取：日期=%s，结果=%s，原因=%s，页数=%s，可用场格=%s，候选=%s，耗时=%sms",
                             play_day, result["status"], result["error"], result["pages"],
                             result["free_slots"], result["candidates"], result["ms"])

    def _availability_failure(self) -> Tuple[str, str]:
        """只对最后一次已完成的读取作库存判断，绝不把探测当成场次读取。"""
        probes = int(self.summary.get("probe_queries", 0))
        attempts = int(self.summary.get("availability_attempts", 0))
        reads = int(self.summary.get("availability_reads", 0))
        prefix = f"接口探测 {probes} 次，场次读取尝试 {attempts} 次、成功 {reads} 次"
        last = self.summary.get("last_availability", {})
        status = last.get("status")
        if self.stopped:
            return "cancelled", prefix + "；任务已取消"
        if status == "no_match":
            return "missed", prefix + "；已读取可用场次，但不符合预约偏好"
        if status == "no_inventory":
            return "missed", prefix + "；最后一次成功读取未发现可用场格"
        return "failed", prefix + f"；未能确认目标日期场次（{last.get('error') or '未完成读取'}），不能判断库存"

    # ------------------------------------------------------------- 放场窗口

    def _grab(self, play_day: str, opening: datetime, choices) -> List[Tuple[str, List[str]]]:
        self.summary.update(probe_queries=0, availability_attempts=0, availability_reads=0,
                            availability_results={}, last_availability={})
        lead = float(self.config.get("runtime.release_probe_lead_ms", 1500)) / 1000
        interval = max(0.05, float(self.config.get("runtime.release_probe_interval_ms", 120)) / 1000)
        grace = float(self.config.get("runtime.release_grace_ms", 4000)) / 1000
        wake_at = opening - timedelta(seconds=lead)
        deadline = opening + timedelta(seconds=grace)
        # 启动晚于窗口时也要给一次查询机会，不能直接判「没抢到」
        now = self._now()
        if now >= deadline:
            deadline = now + timedelta(seconds=1)
            self.logger.warning("启动时间已过放场宽限（%s），仍补查一次", now.isoformat(" "))

        if not self._wait_until(wake_at):
            return []

        # 并行刷新：放场那一刻页面就要开始 reload，和接口探测同时进行，
        # 这样 focus_date 打开时日期栏已经有新日期，省掉一次串行等待（~380ms）。
        reload_thread = None
        if self.config.get("runtime.overlap_release_reload", True):
            try:
                self.query_manager._api_client()   # 预热会话，避免并行时抢 driver
            except Exception:  # noqa: BLE001
                pass
            reload_at = opening + timedelta(milliseconds=30)

            def _reload_worker():
                try:
                    delay = (reload_at - self._now()).total_seconds()
                    if delay > 0:
                        time.sleep(delay)
                    started = time.monotonic()
                    self.query_manager.reload()
                    self.logger.info("[指标] 并行刷新完成：%sms",
                                     int((time.monotonic() - started) * 1000))
                except Exception as error:  # noqa: BLE001
                    self.logger.warning("并行刷新失败（不影响主流程）: %s", error)

            reload_thread = threading.Thread(target=_reload_worker, daemon=True)
            reload_thread.start()

        baseline = None
        backoff = 0.0
        queries = 0
        while not self.stopped and self._now() < deadline:
            now = self._now()
            probe = self.query_manager.probe(play_day)
            queries += 1
            self.summary["probe_queries"] = queries
            self.summary["last_probe"] = probe
            self.logger.info("接口探测 #%s：日期=%s，HTTP=%s，业务码=%s，校验=%s，原因=%s，目标日期=%s，可用场格=%s，响应字符=%s，耗时=%sms",
                             queries, play_day, probe.get("status"), probe.get("business_code"),
                             probe.get("ok", False), probe.get("error"), probe.get("target_date_present", False),
                             probe.get("free_slots"), probe.get("length"), int(probe.get("ms") or 0))
            if probe.get("status") == 200:
                if baseline is None and now < opening:
                    baseline = int(probe.get("length") or 0)
                    self.logger.info("放场前 day/info 体积 %s 字符（仅作诊断基线）", baseline)
                elif released(probe, baseline):
                    self.logger.info(
                        "目标日期接口数据已就绪：%s 字符（基线 %s，%sms）",
                        probe.get("length"), baseline, int(probe.get("ms") or 0),
                    )

            if now >= opening or released(probe, baseline):
                if reload_thread is not None:
                    reload_thread.join(timeout=3)
                self._released_probe = probe
                self._note(state="reading")
                self._log_date_bar_state("放场后")
                found = self._candidates_for(play_day, now, choices)
                if found:
                    return found
                # 日期未就绪、读取失败或无匹配均重试，不预判库存。
                backoff = min(max(backoff * 1.5, 0.4), 1.6)
                if not self._sleep(backoff):
                    return []
                continue

            if not self._sleep(interval):
                return []
        self.summary["probe_queries"] = queries
        if queries == 0:
            self.logger.error("放场窗口内一次查询都没发出")
        return []

    # ------------------------------------------------------------- 主流程

    def run(self, preview: bool = False, dry_run: bool = False) -> bool:
        started = time.monotonic()
        try:
            return self._run(preview, dry_run)
        except Exception as error:  # noqa: BLE001 - 任务级异常也要通知，不能让调度静默失败
            self.logger.error("任务异常: %s", error, exc_info=True)
            self._note(state="failed", message=f"任务异常: {error}")
            self._notify_failure(f"任务异常：{error}")
            return False
        finally:
            self.summary.setdefault("timings", {})["run_ms"] = int((time.monotonic() - started) * 1000)
            self.logger.info("任务结束：状态=%s，总耗时=%sms，接口探测=%s，成功读取=%s，阶段耗时=%s",
                             self.summary.get("state"), self.summary["timings"]["run_ms"],
                             self.summary.get("probe_queries", 0), self.summary.get("availability_reads", 0),
                             self.summary["timings"])
            flow = getattr(self, "submit_captcha", None)
            if flow is not None:
                flow.discard()
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
        self.summary.setdefault("timings", {})["session_ready_ms"] = int((time.monotonic() - started) * 1000)
        self._prewarm(play_day)

        offset = self._clock_offset()
        target = opening - timedelta(seconds=offset)
        if self.config.get("captcha.pre_solve", True):
            if not self._wait_until(target - timedelta(seconds=60)):
                self._note(state="cancelled", message="任务在预解前被取消")
                return False
            self._log_date_bar_state("T-60s")
            if self._presolve_useful(play_day.isoformat()):
                validity = max(0, (target - self._now()).total_seconds()) + 5
                self._pre_solve_captcha(min_validity=validity)
            else:
                self._note(captcha_pre_solve={"state": "skipped",
                                              "message": "目标日期还不在日期栏，预解会被刷新作废"})
        if not self._wait_until(target - timedelta(milliseconds=1500)):
            self._note(state="cancelled", message="任务在放场前被取消")
            return False

        if self.config.get("captcha.pre_solve", True):
            self._validate_pre_solved(min_validity=5)

        self._note(state="grabbing")
        found = self._grab(play_day.isoformat(), target, choices)
        if not found:
            state, message = self._availability_failure()
            self._note(state=state, message=message)
            self.logger.warning("入场日 %s：%s", play_day, message)
            self._notify_failure(f"{play_day}：{message}")
            return False

        self._note(candidates=[{"court": c, "slots": s} for c, s in found])
        self.logger.info("[指标] 放场→候选就绪=%dms（候选 %d 个）",
                         int((self._now() - target).total_seconds() * 1000), len(found))
        for index, (court, slots) in enumerate(found[:max_attempts], start=1):
            if self.stopped:
                self._note(state="cancelled", message="提交前被取消")
                return False
            self.logger.info("候选 %s/%s：%s %s", index, min(len(found), max_attempts), court, ", ".join(slots))
            self.logger.info("[指标] 放场→开始提交=%dms",
                             int((self._now() - target).total_seconds() * 1000))
            started = time.monotonic()
            if index == 1 and self._validate_pre_solved():
                presolved_result = self._submit_presolved(court, slots)
                if presolved_result == "ok":
                    elapsed = int((time.monotonic() - started) * 1000)
                    self.summary.setdefault("timings", {})[f"attempt_{index}_ms"] = elapsed
                    self.summary["presolved_used"] = True
                    self._note(state="done", outcome="created", message="平台已建单（预解验证码），请及时付款")
                    self.logger.info("预订成功（预解）：%s %s（%sms）", court, ", ".join(slots), elapsed)
                    self._notify(play_day, court, slots)
                    # 订单要在同一个浏览器窗口里付款：这里不 quit，窗口留给用户
                    return True
                if presolved_result == "unknown":
                    self._note(state="unknown", outcome="unknown",
                               message="预解提交结果未知，请到平台核对订单")
                    self.logger.error("预解提交结果未知：可能已建单，停止自动重试，请核对订单")
                    self._notify_failure(f"{court} {'、'.join(slots)} 提交结果未知，请核对订单")
                    return False
                self._presolve_fallback("预解路径未提交")
            # confirm_terms=True：抢票必须自己勾「预约须知」，否则 prepareSubmit
            # 会直接返回 terms-unchecked（页面上的须知本来就是未勾选状态）。
            outcome = self._book_candidate(court, slots, partners,
                                           allow_partial=allow_partial, confirm_terms=True)
            elapsed = int((time.monotonic() - started) * 1000)
            self.last_outcome = outcome
            self.summary.setdefault("timings", {})[f"attempt_{index}_ms"] = elapsed
            self.logger.info("[指标] 候选 %s：%s %s → %s（%sms，放场后 %dms）",
                             index, court, ", ".join(slots), outcome.value, elapsed,
                             int((self._now() - target).total_seconds() * 1000))

            if outcome is BookingOutcome.CREATED:
                self._note(state="done", outcome="created", message="平台已建单，请及时付款")
                self.logger.info("预订成功：%s %s（%sms）", court, ", ".join(slots), elapsed)
                self._notify(play_day, court, slots)
                # 订单要在同一个浏览器窗口里付款：这里不 quit，窗口留给用户
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

    def _submit_presolved(self, court: str, slots: List[str]) -> str:
        """选场 → 点提交 → 用预解 token 下单。

        返回 "ok" / "rejected" / "unknown"：
        - rejected：确认没选上场或提交没发出去，可以安全地换候选；
        - unknown：提交可能已经发出去但没拿到可靠回执，必须停下来核对订单。
        """
        if not self._validate_pre_solved():
            return "rejected"
        try:
            selected = self.booking_manager.select_slots(court, slots)
        except Exception as error:  # noqa: BLE001
            self.logger.warning("预解路径选场异常: %s", error)
            return "rejected"
        if selected.get("status") not in ("selected", "partial"):
            self.logger.info("预解路径没选中场格: %s", selected)
            return "rejected"
        if selected.get("status") == "partial" and not self.config.get("booking.allow_partial", False):
            self.logger.warning("预解路径未选齐时段，配置不允许部分提交")
            return "rejected"
        partner = str(self.config.get("user.partner_name") or "").strip()
        if partner:
            self.booking_manager.click_partner([partner])
        started = time.monotonic()
        if not self.submit_captcha.submit_selected():
            self.logger.warning("预解 token 提交动作失败，结果未知")
            return "unknown"
        timeout = float(self.config.get("runtime.submit_timeout_ms", 6000)) / 1000
        ok = self.submit_captcha.wait_success(timeout=timeout)
        self.summary.setdefault("timings", {})["presolved_submit_ms"] = int((time.monotonic() - started) * 1000)
        return "ok" if ok else "unknown"

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
