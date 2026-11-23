"""读取场馆预约表。

核心变化（相对旧版）：
- DOM 读取一次 JS 完成，只返回纯 JSON，不再把 WebElement 传回 Python；
- 等待渲染、等待 day/info 响应都在页面内完成，Python 侧没有 sleep；
- 保留页面上「时段原文」，点击时用原文精确匹配，避免把 08:00~09:00 归一化成
  08:00-09:00 后点不到格子；
- 支持「场地为列 / 场地为行」两种表头；
- 提供 probe()：直接请求页面自己用的 day/info 接口判断是否放场（只做加速信号）。
"""
from __future__ import annotations

import re
import time
from datetime import date as calendar_date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from .api_client import ApiClient
from .page_js import (
    CLICK_DATE_JS,
    FRAMES_JS,
    HELPERS,
    SELECT_CELLS_JS,
    SNAPSHOT_JS,
)
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

BEIJING = timezone(timedelta(hours=8))
TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-–—~～至－]\s*(\d{1,2}:\d{2})")
DATE_LABEL = re.compile(r"(\d{1,2})\s*[月/]\s*(\d{1,2})")
# 前端会按「状态 class + 占用百分比 class」叠加，newReservation = 容量 90% 以上被占
RESERVED_STATES = {
    "reserved", "reservation", "newReservation", "disabled", "selected",
    "sold", "locked", "adminTake",
}


def normalize_clock(value: str) -> str:
    """'8:00' / '08:00' → '08:00'。"""
    hour, minute = value.strip().split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


def parse_slot(label: str) -> Optional[tuple]:
    """从页面时段原文解析 (开始, 结束)，统一成 HH:MM。"""
    match = TIME_RANGE.search(label or "")
    if not match:
        return None
    start, end = normalize_clock(match.group(1)), normalize_clock(match.group(2))
    return (start, end) if start < end else None


def slot_key(label: str) -> Optional[str]:
    bounds = parse_slot(label)
    return f"{bounds[0]}-{bounds[1]}" if bounds else None


class QueryManager:
    """场馆预约表读取器。"""

    def __init__(self, browser):
        self.browser = browser
        self.last_date: Optional[str] = None
        self._helpers_installed = False
        self._cdp = False
        self._api: Optional[ApiClient] = None
        self._api_site: Optional[str] = None
        self._api_expire = 0.0
        self._court_sites: Dict[str, int] = {}   # 场地名 → venueSiteId
        self.last_query_status: Dict[str, Any] = {}
        self.last_page_error: Optional[str] = None

    # ------------------------------------------------------------------ 基础

    @property
    def driver(self):
        return self.browser.driver

    def _js(self, script: str, *args):
        return self.driver.execute_script(script, *args)

    def _async(self, script: str, *args):
        return self.driver.execute_async_script(script, *args)

    def wait(self, predicate, timeout: float = 12):
        return WebDriverWait(
            self.driver, timeout, poll_frequency=0.05,
            ignored_exceptions=(StaleElementReferenceException,),
        ).until(predicate)

    def _wait_false(self, predicate, timeout: float) -> bool:
        try:
            self.wait(predicate, timeout)
            return True
        except TimeoutException:
            return False

    def install(self):
        """把工具函数注入每个新文档（刷新/跳转后依然存在）。"""
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": HELPERS}
            )
            self._cdp = True
        except Exception as error:  # noqa: BLE001 - 非 Chromium 内核时降级
            logger.warning("CDP 注入不可用，将按需注入: %s", error)
            self._cdp = False
        self._helpers_installed = False
        self._ensure_helpers()

    def _ensure_helpers(self):
        if self._helpers_installed:
            return
        try:
            present = self._js("return typeof window.__bb !== 'undefined';")
        except Exception:  # noqa: BLE001
            present = False
        if not present:
            self._js(HELPERS)
        self._helpers_installed = True

    def reset_helpers_cache(self):
        """页面刷新后调用；CDP 注入生效时不需要重新注入。"""
        self._helpers_installed = self._cdp

    # ------------------------------------------------------------- 日期控件

    def tabs(self) -> List[Dict[str, Any]]:
        return self._js(
            """
            return [...document.querySelectorAll('.date_box > div')]
                .filter(function (e) { return e.getClientRects().length; })
                .map(function (e) { return { text: (e.innerText || '').trim(), cls: String(e.className || '') }; });
            """
        ) or []

    def today(self) -> calendar_date:
        value = self._js(
            """
            const format = new Intl.DateTimeFormat('sv-SE', {
                timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
            });
            return format.format(new Date());
            """
        )
        return calendar_date.fromisoformat(value)

    def index_of(self, wanted: str) -> Optional[int]:
        """按「今天 + 序号」推导每个日期页签对应的真实日期。

        站点只显示「12月06日」，所以必须用序号反推年份，否则跨年会把同月同日认错。
        """
        day = calendar_date.fromisoformat(wanted)
        tabs = self.tabs()
        if not tabs:
            return None
        today = self.today()
        for index, tab in enumerate(tabs):
            text = tab.get("text", "")
            match = DATE_LABEL.search(text)
            if not match:
                continue
            month, day_of_month = int(match.group(1)), int(match.group(2))
            expected = today + timedelta(days=index)
            if (month, day_of_month) == (expected.month, expected.day) and expected == day:
                return index
        return None

    def is_date_exist(self, wanted: str) -> bool:
        try:
            return self.index_of(wanted) is not None
        except Exception:  # noqa: BLE001
            return False

    def is_active(self, wanted: str) -> bool:
        index = self.index_of(wanted)
        if index is None:
            return False
        tabs = self.tabs()
        return "active" in tabs[index]["cls"].split()

    def _click_tab(self, index: int, timeout: int = 1200, wanted: Optional[str] = None) -> Dict[str, Any]:
        return self._async(CLICK_DATE_JS, index, timeout, wanted) or {"ok": False, "error": "no-result"}

    def _loaded_response(self, wanted: str) -> Optional[Dict[str, Any]]:
        """整页刚刷新且目标已选中时，使用当前文档已校验的首次响应。"""
        return self._js("""
            const hits = window.__bb && window.__bb.dayResponses || [];
            return [...hits].reverse().find(item => item.date === arguments[0]) || null;
        """, wanted)

    def _wait_loaded_response(self, wanted: str) -> Optional[Dict[str, Any]]:
        """默认选中的日期可能仍在加载，等当前文档的首次响应。"""
        try:
            return self.wait(lambda _: self._loaded_response(wanted), 3)
        except TimeoutException:
            return None

    def reload(self):
        self.driver.refresh()
        self.reset_helpers_cache()
        self.last_date = None
        self._ensure_helpers()
        self._wait_false(lambda _: self.tabs(), 15)

    def open_venue_site(self, site_id: int) -> bool:
        """直接打开某个场馆页面（按 venueSiteId）。仅作排障用途，日常轮询不会切站点。

        同一个「羽毛球」下按 venueSiteId 分成多张表，页面一次只显示一个；
        轮询要覆盖全部场地就得挨个切。
        """
        try:
            origin = self._js("return location.origin;")
        except Exception as error:  # noqa: BLE001
            logger.warning("读取 origin 失败: %s", error)
            return False
        if not origin:
            return False
        self.driver.get(f"{origin}/venue/venue-reservation/{int(site_id)}")
        self.reset_helpers_cache()
        self.last_date = None
        self.last_query_status = {}
        self._api = None
        self._api_site = str(int(site_id))
        if not self._wait_false(lambda _: self.tabs(), 15):
            logger.warning("子场馆 %s 没有加载出日期条", site_id)
            return False
        self._ensure_helpers()
        return True

    def focus_date(self, wanted: str, fresh: bool = False, refresh: str = "auto") -> bool:
        """让预约表显示 wanted 这一天的数据。fresh=True 时强制重新取一次数据。"""
        self.last_date = None
        self.last_query_status = {"ok": False, "date": wanted, "error": None}
        if not self._wait_false(lambda _: self.tabs(), 15):
            self.last_query_status["error"] = "date-bar-missing"
            logger.error("页面上没有日期选择条，可能还没进入场馆页面")
            return False
        self._ensure_helpers()
        index = self.index_of(wanted)
        if index is None:
            before = [tab.get("text", "")[:40] for tab in self.tabs()]
            logger.info("目标日期 %s 未出现在页面，刷新日期窗口（当前日期栏=%s）", wanted, before)
            # 开售前打开的日期栏不会被 Python 接口探测更新，必须先刷新再定位。
            started = time.monotonic()
            self.reload()
            # 日期栏是异步渲染的：刷新后通常先出现旧日期，新日期要晚几十到几百毫秒。
            # 轮询等它真正出现，而不是刷新完只看一眼（否则会误判 target-date-missing）。
            appeared = self._wait_false(
                lambda _: self.index_of(wanted) is not None, 5
            )
            index = self.index_of(wanted)
            if appeared and index is not None:
                logger.info("目标日期 %s 在刷新后出现，累计 %sms",
                            wanted, int((time.monotonic() - started) * 1000))
            self.last_query_status["refresh_ms"] = int((time.monotonic() - started) * 1000)
            self.last_query_status["refreshed"] = True
            self.last_query_status["visible_dates"] = [tab.get("text", "")[:40] for tab in self.tabs()]
            logger.info("日期窗口刷新结束：目标=%s，就绪=%s，耗时=%sms，日期栏=%s", wanted,
                        index is not None, self.last_query_status["refresh_ms"],
                        self.last_query_status["visible_dates"])
            if index is None:
                self.last_query_status["error"] = "target-date-missing"
                return False

        active = "active" in self.tabs()[index]["cls"].split()
        if active and not fresh:
            self.last_date = wanted
            self.last_query_status["ok"] = True
            return True

        if refresh == "reload" and not self.last_query_status.get("refreshed"):
            refresh_started = time.monotonic()
            self.reload()
            self.last_query_status["refreshed"] = True
            self.last_query_status["refresh_ms"] = int((time.monotonic() - refresh_started) * 1000)
            logger.info("目标日期 %s 强制刷新完成，耗时=%sms", wanted, self.last_query_status["refresh_ms"])
            index = self.index_of(wanted)
            if index is None:
                self.last_query_status["error"] = "target-date-missing"
                return False

        started_at = datetime.now(BEIJING)
        loaded = (self._wait_loaded_response(wanted) if self.last_query_status.get("refreshed")
                  and "active" in self.tabs()[index]["cls"].split() else None)
        result = loaded or self._click_tab(index, wanted=wanted)
        if not result.get("ok") and refresh == "auto" and not self.last_query_status.get("refreshed"):
            # 部分前端在页签已选中时不会再发请求，退化成整页刷新
            logger.info("点击日期页签没有触发数据请求（%s），改用整页刷新", result.get("error"))
            refresh_started = time.monotonic()
            self.reload()
            self.last_query_status["refreshed"] = True
            self.last_query_status["refresh_ms"] = int((time.monotonic() - refresh_started) * 1000)
            index = self.index_of(wanted)
            if index is None:
                self.last_query_status["error"] = "target-date-missing"
                return False
            loaded = (self._wait_loaded_response(wanted)
                      if "active" in self.tabs()[index]["cls"].split() else None)
            result = loaded or self._click_tab(index, wanted=wanted)

        if not result.get("ok"):
            self.last_query_status["error"] = result.get("error") or "date-response-missing"
            logger.warning("目标日期 %s 点击后没有确认数据响应，不能认定场次读取成功", wanted)
            return False

        if not self._wait_false(lambda _: self.is_active(wanted), 8):
            self.last_query_status["error"] = "date-not-active"
            logger.error("日期 %s 没有切换到选中状态", wanted)
            return False

        # 等两帧，确保 Vue 已经把新数据写进 DOM
        try:
            self._async(FRAMES_JS)
        except Exception:  # noqa: BLE001
            pass

        self.last_date = wanted
        self.last_query_status["ok"] = True
        self.last_query_status["response_ms"] = result.get("ms")
        logger.debug(
            "日期 %s 就绪，数据响应耗时 %sms，整体 %sms",
            wanted, result.get("ms"), int((datetime.now(BEIJING) - started_at).total_seconds() * 1000),
        )
        return True

    # --------------------------------------------------------------- 读取数据

    def snapshot(self) -> Optional[Dict[str, Any]]:
        page = self._async(SNAPSHOT_JS) or {}
        if not isinstance(page, dict) or page.get("error"):
            return None
        labels, rows = page.get("labels"), page.get("rows")
        if (not isinstance(labels, list) or len(labels) < 2
                or any(not isinstance(label, str) for label in labels)
                or any(not label.strip() for label in labels[1:])
                or not isinstance(rows, list)):
            return None
        reversed_axes = bool(page.get("reversed"))
        if reversed_axes and any(parse_slot(label) is None for label in labels[1:]):
            return None
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("label"), str)
                    or not row["label"].strip() or not isinstance(row.get("cells"), list)
                    or len(row["cells"]) != len(labels) - 1
                    or (not reversed_axes and parse_slot(row["label"]) is None)):
                return None
            if any(cell is not None and (not isinstance(cell, dict)
                    or not isinstance(cell.get("classes"), list)) for cell in row["cells"]):
                return None
        return page

    @staticmethod
    def free_slots(page: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """{场地: {标准时段: 页面原文}}，只保留可预订的格子。"""
        labels = page.get("labels") or []
        reversed_axes = bool(page.get("reversed"))
        inventory: Dict[str, Dict[str, str]] = {}

        for row in page.get("rows") or []:
            row_label = row.get("label") or ""
            for offset, cell in enumerate(row.get("cells") or [], start=1):
                if not cell or offset >= len(labels):
                    continue
                if reversed_axes:
                    court, slot = row_label, labels[offset]
                else:
                    court, slot = labels[offset], row_label
                court = court.replace("→", "").strip()
                key = slot_key(slot)
                if not court or not key:
                    continue
                states = set(cell.get("classes") or [])
                if "free" in states and not states.intersection(RESERVED_STATES):
                    inventory.setdefault(court, {})[key] = slot
        return inventory

    def _turn(self, page: Dict[str, Any], direction: str) -> Optional[Dict[str, Any]]:
        """翻一页。direction 是 'prev' / 'next'。"""
        script = """
        const [direction] = arguments;
        const visible = e => e && e.getClientRects().length;
        const table = [...document.querySelectorAll('.reserveTable .spaceTable table')].filter(visible)[0];
        if (!table || !table.tHead) { return false; }
        const head = table.tHead.rows[0];
        const arrow = [...head.querySelectorAll('.ivu-icon-ios-arrow-' + (direction === 'prev' ? 'back' : 'forward'))]
            .filter(visible)[0];
        if (!arrow) { return false; }
        arrow.click();
        return true;
        """
        if not self._js(script, direction):
            return None
        before = tuple(page.get("labels") or [])
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            candidate = self.snapshot()
            if candidate and tuple(candidate.get("labels") or []) != before:
                return candidate
            time.sleep(0.04)
        return None

    @staticmethod
    def page_courts(page: Dict[str, Any]) -> List[str]:
        """当前页出现过的场地名（含已售罄的），用于翻页定位。"""
        labels = page.get("labels") or []
        if page.get("reversed"):
            return [(row.get("label") or "").strip() for row in page.get("rows") or []]
        return [label.replace("→", "").strip() for label in labels[1:]]

    def first_page(self) -> Optional[Dict[str, Any]]:
        page = self.snapshot()
        if not page:
            return None
        guard = 0
        while page.get("prev") and guard < 8:
            nxt = self._turn(page, "prev")
            if not nxt:
                self.last_page_error = "pagination-rewind-failed"
                return page
            page, guard = nxt, guard + 1
        if page.get("prev"):
            self.last_page_error = "pagination-limit"
        return page

    def iter_pages(self) -> Iterator[Dict[str, Any]]:
        self.last_page_error = None
        page = self.first_page()
        if not page:
            self.last_page_error = "table-missing"
            return
        seen = set()
        while True:
            key = tuple(page.get("labels") or [])
            if key in seen:
                self.last_page_error = "pagination-repeated"
                logger.warning("分页出现重复表头，停止遍历")
                return
            seen.add(key)
            yield page
            if not page.get("next"):
                return
            nxt = self._turn(page, "next")
            if not nxt:
                self.last_page_error = "pagination-failed"
                return
            page = nxt

    def scan(self, wanted: str) -> Dict[str, Dict[str, str]]:
        """读完整张表（必要时翻页），返回 {场地: {时段: 原文}}。"""
        if not self.focus_date(wanted, fresh=True):
            return {}
        if not self._wait_false(lambda _: self.is_active(wanted) and self.snapshot(), 3):
            self.last_query_status.update(ok=False, error="table-not-ready")
            return {}
        merged: Dict[str, Dict[str, str]] = {}
        for page in self.iter_pages():
            for court, slots in self.free_slots(page).items():
                merged.setdefault(court, {}).update(slots)
        if self.last_page_error:
            self.last_query_status.update(ok=False, error=self.last_page_error)
            return {}
        return merged

    def ensure_visible(self, court: str) -> bool:
        """确保目标场地所在的页是当前页（翻页）。"""
        page = self.snapshot()
        if not page:
            return False
        if court in self.page_courts(page):
            return True
        for candidate in self.iter_pages():
            if court in self.page_courts(candidate):
                return True
        return False

    # ------------------------------------------------------------- 对外兼容层

    def _day_rows(self, site: int, day: str) -> Optional[list]:
        client = self._api_client()
        if client is None:
            return None
        try:
            data = client.json_get(
                "/api/reservation/day/info",
                {"venueSiteId": str(site), "searchDate": day, "hasReserveInfo": "1",
                 "nocache": str(int(time.time() * 1000))},
            ) or {}
        except Exception as error:  # noqa: BLE001
            logger.debug("读取场馆 %s 的 day/info 失败: %s", site, error)
            return None
        dates = data.get("reservationDateSpaceInfo") if isinstance(data, dict) else None
        rows = (dates or {}).get(day)
        return rows if isinstance(rows, list) else None

    def slot_status(self, day: str, court: str, slot: str) -> Optional[int]:
        """读 day/info 里某个场格的状态：1 可订 / 2 不开放 / 3 未支付 / 4 已预约。

        同一批场地可能落在不同 venueSiteId 下，当前页面只对应其中一个，
        所以这里按需挨个查，并缓存「场地 → site」。
        读不到返回 None。
        """
        bounds = parse_slot(str(slot).replace("～", "-").replace("－", "-"))
        if bounds is None:
            return None
        wanted = (court or "").replace("→", "").strip()
        if not wanted:
            return None

        sites: List[int] = []
        cached = self._court_sites.get(wanted)
        if isinstance(cached, int):
            sites.append(cached)
        if self._api_site:
            try:
                current = int(self._api_site)
                if current not in sites:
                    sites.append(current)
            except (TypeError, ValueError):
                pass
        sites.extend(site for site in range(1, 7) if site not in sites)

        for site in sites:
            rows = self._day_rows(site, day)
            if not rows:
                continue
            for row in rows:
                for _, cell in (row or {}).items():
                    if not isinstance(cell, dict):
                        continue
                    name = (cell.get("spaceName") or "").replace("→", "").strip()
                    if name != wanted:
                        continue
                    start = str(cell.get("startDate") or "")
                    end = str(cell.get("endDate") or "")
                    if " " not in start or " " not in end:
                        continue
                    if start.split(" ")[1][:5] != bounds[0] or end.split(" ")[1][:5] != bounds[1]:
                        continue
                    status = cell.get("reservationStatus")
                    if isinstance(status, int):
                        self._court_sites[wanted] = site
                        return status
        return None

    def api_free_slots(self, day: str) -> Optional[Dict[str, Dict[str, str]]]:
        """直接用 day/info 拿可订场格（不经过 DOM）。失败返回 None。

        放场瞬间用它能省掉「等表格渲染 + 翻页解析」的一两秒；
        返回结构和 DOM 版一致：{场地: {时段: 页面原文}}。
        """
        client = self._api_client()
        if client is None or not self._api_site:
            return None
        try:
            slots = client.free_slots(self._api_site, day)
        except Exception as error:  # noqa: BLE001
            logger.debug("接口取候选失败: %s", error)
            return None
        return slots if isinstance(slots, dict) else None

    def query_available_courts(self, wanted: str) -> Dict[str, List[str]]:
        return {court: sorted(slots) for court, slots in self.scan(wanted).items()}

    def parse_court_availability(self) -> Dict[str, List[str]]:
        if not self.last_date:
            raise RuntimeError("请先选择日期")
        inventory: Dict[str, List[str]] = {}
        for page in self.iter_pages():
            for court, slots in self.free_slots(page).items():
                inventory.setdefault(court, []).extend(sorted(slots))
        return {court: sorted(set(slots)) for court, slots in inventory.items()}

    def probe(self, wanted: str) -> Dict[str, Any]:
        """直接请求 day/info 判断是否放场（带签名，约 30~60ms）。

        这是页面自己用的接口，返回体积在放场瞬间会明显变化，用来做放场探测。
        拿不到会话信息时返回 status=0，调用方会退化成「点日期页签刷新」。
        """
        client = self._api_client()
        if client is None or not self._api_site:
            return {"status": 0, "length": 0, "error": "no-session"}
        started = time.monotonic()
        params = {
            "venueSiteId": self._api_site,
            "searchDate": wanted,
            "hasReserveInfo": "1",
            "nocache": str(int(time.time() * 1000)),
        }
        status, body = client.get("/api/reservation/day/info", params)
        return {
            "status": status,
            "length": len(body or ""),
            "ms": (time.monotonic() - started) * 1000,
            **ApiClient.inspect_day_response(status, body, wanted),
        }

    def _api_client(self) -> Optional[ApiClient]:
        """从页面拿 origin / 会话 token / venueSiteId，缓存 5 分钟。"""
        if self._api is not None and time.monotonic() < self._api_expire:
            return self._api
        try:
            origin = self._js("return location.origin;")
            token = self._js("return localStorage.getItem('dataSix') || '';") or ""
            match = re.search(r"venue-reservation/(\d+)", self.driver.current_url or "")
            site = match.group(1) if match else ""
            if not origin or not token:
                return None
            self._api = ApiClient(origin, token)
            self._api_site = site
            self._api_expire = time.monotonic() + 300
            logger.info("接口会话已就绪（venueSiteId=%s）", site or "未知")
            return self._api
        except Exception as error:  # noqa: BLE001
            logger.debug("获取接口会话失败: %s", error)
            return None

    def click_close_button(self) -> bool:
        return bool(self._js(
            """
            const btn = [...document.querySelectorAll('.ivu-modal-wrap button, .ivu-modal-wrap .ivu-modal-close')]
                .filter(function (e) { return e.getClientRects().length; })[0];
            if (!btn) { return false; }
            btn.click();
            return true;
            """
        ))

    # 兼容旧调用方
    def select_date(self, wanted: str) -> bool:
        return self.focus_date(wanted, fresh=False)
