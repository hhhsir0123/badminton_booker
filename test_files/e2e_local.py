"""本地端到端验证：用真 Chrome + 假场馆页，跑通 读表 → 选场 → 提交 全链路。

为什么要这个：
- 真站点需要账号和校园网，不适合每次改动都验证；
- 这个 fixture 复刻了真实页面的关键结构：日期条、预约表、场格 class、提交区，
  并且用本地 HTTP 提供 day/info 接口，所以「点日期 → 发请求 → 重渲染」也能真实发生。

覆盖：
1. QueryManager.install / focus_date / snapshot / free_slots / probe
2. 原始时段文本（19:00~20:00）能被正确点击（而不是归一化成 19:00-20:00 后找不到）
3. BookingManager.select_slots / book_court → BookingOutcome.CREATED
4. 每一步耗时

用法：
    python test_files/e2e_local.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.core.booking_result import BookingOutcome  # noqa: E402
from src.core.court_booking import BookingManager  # noqa: E402
from src.core.court_query import QueryManager  # noqa: E402

CHROME_CANDIDATES = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]
CDP_PORT = 9333
HTTP_PORT = 8899
PROFILE_DIR = os.path.join(ROOT, "verification", ".chrome")
PAGE_URL = f"http://127.0.0.1:{HTTP_PORT}/venue"


# --------------------------------------------------------------------- 页面
def build_fixture() -> str:
    today = date.today()
    tabs = []
    for offset in range(3):
        day = today + timedelta(days=offset)
        tabs.append(
            f'<div data-date="{day.isoformat()}" class="{"active" if offset else ""}">'
            f"<span>{day.month}月{day.day:02d}日</span></div>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>场馆预约</title>
<style>body{{font:14px sans-serif}}.date_box{{display:flex;gap:8px}}
.date_box>div{{padding:6px 10px;border:1px solid #ccc;cursor:pointer}}
.date_box>div.active{{background:#14684a;color:#fff}}
.reserveBlock{{padding:4px 6px;border:1px solid #ccc;cursor:pointer}}
.reserveBlock.free{{background:#e3f0e9}}
.reserveBlock.selected{{background:#14684a;color:#fff}}
.reserveBlock.reserved{{background:#eee;color:#999}}
.ivu-message{{position:fixed;top:10px;right:10px;background:#fff;border:1px solid #14684a;padding:6px 10px}}
</style></head>
<body>
<div class="date_box">{''.join(tabs)}</div>
<div class="reserveTable"><div class="spaceTable">
<table>
<thead><tr><td>时间段</td><td>训练馆3号</td><td>训练馆4号</td></tr></thead>
<tbody>
<tr><td>19:00~20:00</td><td><div class="reserveBlock free">30</div></td><td><div class="reserveBlock free">30</div></td></tr>
<tr><td>20:00~21:00</td><td><div class="reserveBlock free">30</div></td><td><div class="reserveBlock reserved">已售</div></td></tr>
</tbody></table>
</div></div>
<div class="submit_order_box pc"><div class="btn disabled" id="submit">提交订单</div></div>
<script>
const tabs = [...document.querySelectorAll('.date_box > div')];
tabs.forEach(function (tab) {{
  tab.addEventListener('click', function () {{
    tabs.forEach(function (t) {{ t.classList.remove('active'); }});
    tab.classList.add('active');
    fetch('/api/reservation/day/info?searchDate=' + tab.dataset.date, {{cache: 'no-store'}})
      .then(function (r) {{ return r.json(); }})
      .then(applyData);
  }});
}});

function applyData(data) {{
  const body = document.querySelector('tbody');
  body.innerHTML = '';
  data.rows.forEach(function (row) {{
    const tr = document.createElement('tr');
    const head = document.createElement('td');
    head.textContent = row.slot;
    tr.appendChild(head);
    row.cells.forEach(function (free) {{
      const td = document.createElement('td');
      const block = document.createElement('div');
      block.className = 'reserveBlock ' + (free ? 'free' : 'reserved');
      block.textContent = free ? '30' : '已售';
      td.appendChild(block);
      tr.appendChild(td);
    }});
    body.appendChild(tr);
  }});
  bindCells();
}}

function bindCells() {{
  [...document.querySelectorAll('.reserveBlock')].forEach(function (block) {{
    block.addEventListener('click', function () {{
      if (block.classList.contains('reserved')) {{ return; }}
      block.classList.add('selected');
      document.getElementById('submit').classList.remove('disabled');
    }});
  }});
}}

document.getElementById('submit').addEventListener('click', function () {{
  if (this.classList.contains('disabled')) {{ return; }}
  const toast = document.createElement('div');
  toast.className = 'ivu-message';
  toast.textContent = '预约成功，请尽快支付';
  document.body.appendChild(toast);
}});

bindCells();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/reservation/day/info"):
            payload = json.dumps(
                {
                    "rows": [
                        {"slot": "19:00~20:00", "cells": [True, True]},
                        {"slot": "20:00~21:00", "cells": [True, False]},
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        body = build_fixture().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# --------------------------------------------------------------------- CDP
class CDPDriver:
    """只实现本项目用到的那部分 WebDriver 接口，底层走 CDP。"""

    def __init__(self, ws_url: str):
        import websocket

        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0
        self._send("Page.enable")
        self._send("Runtime.enable")

    def _send(self, method, params=None):
        self._id += 1
        message_id = self._id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == message_id:
                if "error" in data:
                    raise RuntimeError(f"CDP {method} 失败: {data['error']}")
                return data.get("result", {})

    def _eval(self, expression: str, await_promise: bool = False):
        result = self._send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            raise RuntimeError(f"页面脚本异常: {detail.get('text')} {detail.get('exception', {}).get('description', '')}")
        return result.get("result", {}).get("value")

    def execute_script(self, script, *args):
        expression = f"(function(){{\n{script}\n}}).apply(null, {json.dumps(args)})"
        return self._eval(expression)

    def execute_async_script(self, script, *args):
        expression = (
            "new Promise(function (resolve) {\n"
            "  var done = function (value) { resolve(value === undefined ? null : value); };\n"
            f"  (function(){{\n{script}\n}}).apply(null, {json.dumps(args)}.concat([done]));\n"
            "})"
        )
        return self._eval(expression, await_promise=True)

    def execute_cdp_cmd(self, method, params=None):
        return self._send(method, params)

    def get(self, url: str):
        self._send("Page.navigate", {"url": url})
        self.wait_ready()

    def refresh(self):
        self._send("Page.reload", {})
        self.wait_ready()

    def wait_ready(self, timeout: float = 15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._eval("document.readyState") == "complete":
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)
        raise TimeoutError("页面加载超时")

    @property
    def current_url(self):
        return self._eval("location.href")

    def set_page_load_timeout(self, *args):
        pass

    def set_script_timeout(self, *args):
        pass

    def save_screenshot(self, path):
        data = self._send("Page.captureScreenshot", {"format": "png"})
        import base64

        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data["data"]))


# --------------------------------------------------------------------- 工具
class Browser:
    def __init__(self, driver):
        self.driver = driver


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def free_port(start: int) -> int:
    for port in range(start, start + 50):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def wait_cdp(port: int, timeout: float = 20.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
                targets = json.loads(response.read())
            for target in targets:
                if target.get("type") == "page":
                    return target["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise TimeoutError("Chrome 的 CDP 端口没起来")


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("FAIL  找不到 Chrome")
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    os.makedirs(PROFILE_DIR, exist_ok=True)
    profile = os.path.join(PROFILE_DIR, "run-" + uuid.uuid4().hex[:8])
    os.makedirs(profile, exist_ok=True)

    process = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--no-proxy-server",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    failures = []
    try:
        ws_url = wait_cdp(CDP_PORT)
        driver = CDPDriver(ws_url)
        browser = Browser(driver)
        query = QueryManager(browser)

        target = (date.today() + timedelta(days=2)).isoformat()

        print("1) 打开页面")
        driver.get(PAGE_URL)
        print(f"   URL = {driver.current_url}")

        print("2) 注入页面工具 + 切换日期")
        started = time.monotonic()
        query.install()
        ok = query.focus_date(target, fresh=True)
        ready_ms = int((time.monotonic() - started) * 1000)
        print(f"   focus_date({target}) = {ok}，耗时 {ready_ms}ms")
        if not ok:
            failures.append("focus_date 失败")

        print("3) 读取预约表")
        started = time.monotonic()
        page = query.snapshot()
        snap_ms = int((time.monotonic() - started) * 1000)
        if not page:
            failures.append("snapshot 为空")
            print("   FAIL snapshot 为空")
        else:
            labels = page.get("labels")
            print(f"   表头 = {labels}，行数 = {len(page.get('rows') or [])}，耗时 {snap_ms}ms")
            if labels != ["时间段", "训练馆3号", "训练馆4号"]:
                failures.append(f"表头不对: {labels}")

        free = query.free_slots(page) if page else {}
        print(f"   可订场次 = {json.dumps(free, ensure_ascii=False)}")
        expected = {
            "训练馆3号": {"19:00-20:00": "19:00~20:00", "20:00-21:00": "20:00~21:00"},
            "训练馆4号": {"19:00-20:00": "19:00~20:00"},
        }
        if free != expected:
            failures.append(f"free_slots 不符预期: {free}")

        print("4) 放场探测（day/info）")
        probe = query.probe(target)
        print(f"   probe = status={probe.get('status')} length={probe.get('length')} ms={probe.get('ms'):.1f}")
        if probe.get("status") != 200 or not probe.get("length"):
            failures.append(f"probe 异常: {probe}")

        print("5) 选场（用页面原文 19:00~20:00）")
        booking = BookingManager(browser, query)
        started = time.monotonic()
        selection = booking.select_slots("训练馆3号", ["19:00~20:00"])
        select_ms = int((time.monotonic() - started) * 1000)
        print(f"   {selection}，耗时 {select_ms}ms")
        if selection.get("status") != "selected":
            failures.append(f"选场失败: {selection}")

        print("6) 提交订单")
        started = time.monotonic()
        outcome = booking.book_court("训练馆3号", ["19:00~20:00"])
        submit_ms = int((time.monotonic() - started) * 1000)
        print(f"   BookingOutcome = {outcome.name}，耗时 {submit_ms}ms")
        if outcome is not BookingOutcome.CREATED:
            failures.append(f"提交结果不是 CREATED: {outcome}")

        print("7) 汇总")
        print(f"   读表 {ready_ms}ms | 快照 {snap_ms}ms | 选场 {select_ms}ms | 提交 {submit_ms}ms")

        driver.save_screenshot(os.path.join(ROOT, "verification", "final.png"))
        print("   截图: verification/final.png")
    except Exception as error:  # noqa: BLE001
        failures.append(f"异常: {error}")
        import traceback

        traceback.print_exc()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:  # noqa: BLE001
            process.kill()
        server.shutdown()

    if failures:
        print("\nFAILED:")
        for item in failures:
            print(" -", item)
        return 1
    print("\nALL PASS：读表 → 选场 → 提交 全链路跑通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
