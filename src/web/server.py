"""本地控制台：静态配置页 + 启停 API。

浏览器不能直接启动进程，所以由这个本地服务把「页面按钮」翻译成「启停动作」：

    GET  /                 配置页（复用 src/web 下的静态文件）
    GET  /api/status       运行状态 + 最近日志
    GET  /api/config       读出当前 config.yaml
    POST /api/config       保存页面提交的 YAML（校验后原子写入）
    POST /api/start        开始抢票 / 开始轮询
    POST /api/stop         停止

只用标准库，不引入 Flask 之类的依赖。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ..config import Config, validate_config, validate_for_run
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/config-io.js": ("config-io.js", "application/javascript; charset=utf-8"),
}


class _LogBuffer(logging.Handler):
    """把日志收进环形缓冲；额外挑出「核心日志」给页面展示。"""

    CORE_PREFIXES = ("src.", "__main__")

    def __init__(self, buffer: deque, core: deque, maxlen: int = 400):
        super().__init__()
        self.buffer = buffer
        self.core = core
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.buffer.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass
        # 核心日志：只留本项目自己的 INFO 及以上，去掉 logger 名
        if record.levelno < logging.INFO:
            return
        if not str(record.name).startswith(self.CORE_PREFIXES):
            return
        try:
            stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            self.core.append(f"{stamp} {record.getMessage()}")
        except Exception:  # noqa: BLE001
            pass


class Runner:
    """管一条运行中的流程（抢票或轮询），支持随时停止。"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.thread: threading.Thread | None = None
        self.pipeline = None
        self.state = "idle"
        self.mode = ""
        self.message = ""
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.logs: deque = deque(maxlen=400)
        self.core: deque = deque(maxlen=200)
        self._stop_requested = False
        self.closing = False
        self._remaining_windows = []
        self._lock = threading.Lock()
        handler = _LogBuffer(self.logs, self.core)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)

    # ------------------------------------------------------------- 状态

    def status(self) -> dict:
        alive = bool(self.thread and self.thread.is_alive())
        message = self.message
        pipeline = self.pipeline
        summary = getattr(pipeline, "summary", None) or {} if pipeline is not None else {}
        if summary:
            message = summary.get("message") or message
        return {
            "state": "running" if alive else self.state,
            "mode": self.mode,
            "message": message,
            "summary": summary,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": int(time.time() - self.started_at) if self.started_at and alive else None,
            "logs": list(self.logs)[-120:],
            "core": list(self.core)[-80:],
        }

    # ------------------------------------------------------------- 启停

    def start(self, mode: str) -> tuple[bool, str]:
        with self._lock:
            if self.closing:
                return False, "控制台正在退出"
            if self.thread and self.thread.is_alive():
                return False, "已经在运行了"
            config = Config(self.config_path)
            errors = validate_for_run(config.config)
            if errors:
                return False, "配置有误：" + "；".join(errors)

            self._stop_requested = False
            self.state = "running"
            self.mode = mode
            self.message = "已启动"
            self.started_at = time.time()
            self.finished_at = None
            self.thread = threading.Thread(target=self._run, args=(mode,), daemon=True)
            self.thread.start()
            return True, "已启动"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if self.thread is None:
                return False, "没有在运行"
            self._stop_requested = True
            pipeline = self.pipeline
        try:
            if pipeline is not None:
                pipeline.stop()
        except Exception as error:  # noqa: BLE001
            logger.warning("停止失败: %s", error)
        self.message = "正在停止…"
        return True, "正在停止"

    def _run(self, mode: str):
        pipeline = None
        try:
            if mode == "poll":
                from ..pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline

                pipeline = PeriodicScanningPipeline(self.config_path)
            else:
                from ..pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline

                pipeline = TicketGrabbingPipeline(self.config_path)
            with self._lock:
                self.pipeline = pipeline
                cancelled = self._stop_requested
            if cancelled:
                pipeline.stop()
            ok = False if cancelled else pipeline.run()
            if self._stop_requested:
                self.state = "stopped"
                self.message = "已停止"
            else:
                self.state = "done" if ok else "failed"
                self.message = "已完成" if ok else "结束（未成功）"
        except Exception as error:  # noqa: BLE001
            logger.error("运行异常: %s", error, exc_info=True)
            self.state = "failed"
            self.message = f"异常：{error}"
        finally:
            try:
                if pipeline is not None and (self._stop_requested or self.closing):
                    pipeline.cleanup()
            finally:
                with self._lock:
                    browser = getattr(pipeline, "browser", None)
                    if browser is not None:
                        self._remaining_windows.append(browser)
                    self.finished_at = time.time()
                    self.pipeline = None
                    self.thread = None

    def begin_close(self):
        with self._lock:
            self.closing = True
            thread = self.thread
        self.stop()
        return thread

    def close(self):
        thread = self.begin_close()
        if thread is not None:
            thread.join()
        for browser in self._remaining_windows:
            try:
                browser.close()
            except Exception as error:
                logger.warning("关闭预约窗口失败: %s", error)
        self._remaining_windows.clear()




class LoginChecker:
    """真实登录一次来验证账号密码。

    - 填了密码：用临时 profile 走完整登录（含验证码 OCR），验证完删除临时目录；
    - 没填密码：用已保存的 profile 打开页面，看是否还是登录态。
    """

    def __init__(self, config_path: str, project_root: str):
        self.config_path = config_path
        self.project_root = project_root
        self._lock = threading.Lock()
        self._closing = threading.Event()

    def close(self):
        self._closing.set()
        with self._lock:
            pass  # The active login check owns its browser and closes it before releasing.

    def check(self, username: str, password: str) -> dict:
        if not username:
            return {"ok": False, "message": "请先填写登录手机号"}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": "上一次验证还在进行中，请稍等"}
        if self._closing.is_set():
            self._lock.release()
            return {"ok": False, "message": "控制台正在退出"}
        started = time.monotonic()
        profile = ""
        temporary = False
        try:
            from ..core.browser import BrowserManager
            from ..core.login import LoginManager
            from ..utils.captcha_solver import CaptchaSolver
            from ..utils.ocr import CaptchaRecognizer

            config = Config(self.config_path)
            url = str(config.get("login.url") or "https://ggtypt.cumtb.edu.cn/venue/login")

            if password:
                import uuid

                profile = os.path.join(self.project_root, "logs", f".login-check-{uuid.uuid4().hex[:8]}")
                temporary = True
            else:
                profile = str(config.get("login.profile_dir") or ".chrome-profile")
                if not os.path.isabs(profile):
                    profile = os.path.join(self.project_root, profile)

            browser = BrowserManager(headless=True, profile_dir=profile, project_root=self.project_root)
            driver = browser.start()
            try:
                if not password:
                    driver.get(url)
                    time.sleep(1.5)
                    if driver.find_elements("css selector", "input[type='password']"):
                        return {"ok": False, "message": "保存的登录状态已失效，请填写密码后重试"}
                    return {"ok": True, "message": "已用保存的登录状态验证通过"}

                recognizer = CaptchaRecognizer()
                solver = CaptchaSolver(browser, recognizer, max_attempts=3)
                login = LoginManager(browser, recognizer, solver)
                ok = login.ensure_logged_in(
                    url=url, username=username, password=password, auto_captcha=True, max_retry=2
                )
                elapsed = int((time.monotonic() - started) * 1000)
                if ok and "login" not in driver.current_url:
                    return {"ok": True, "message": f"登录成功（{elapsed}ms）"}
                if ok:
                    return {"ok": True, "message": f"登录成功（{elapsed}ms）"}
                return {"ok": False, "message": "登录失败：账号或密码错误，或验证码未通过"}
            finally:
                try:
                    driver.quit()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as error:  # noqa: BLE001
            logger.warning("登录验证异常: %s", error, exc_info=True)
            return {"ok": False, "message": f"登录验证异常：{error}"}
        finally:
            if temporary and profile:
                import shutil

                shutil.rmtree(profile, ignore_errors=True)
            self._lock.release()

def save_config_text(config_path: str, text: str) -> None:
    """校验并保存页面提交的 YAML；不合法抛 ValueError。"""
    import yaml as _yaml

    from ..config import DEFAULTS, deep_merge

    parsed = _yaml.safe_load(text) or {}
    if not isinstance(parsed, dict):
        raise ValueError("配置顶层必须是字典")
    config = Config(config_path)
    config.config = deep_merge(DEFAULTS, parsed)
    errors = validate_for_run(config.config)
    if errors:
        raise ValueError("；".join(errors))
    config.save()


def _handler_factory(runner: Runner, checker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BadmintonBooker/1.0"

        def log_message(self, fmt, *args):  # 静音默认访问日志
            return

        # --------------------------------------------------------- 工具

        def _send(self, code: int, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict):
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                return {}

        # --------------------------------------------------------- 路由

        def do_GET(self):
            path = urlparse(self.path).path
            if path in STATIC_FILES:
                name, ctype = STATIC_FILES[path]
                try:
                    with open(os.path.join(WEB_DIR, name), "rb") as handle:
                        self._send(200, handle.read(), ctype)
                except FileNotFoundError:
                    self._send(404, b"not found", "text/plain")
                return
            if path == "/api/status":
                self._json(200, runner.status())
                return
            if path == "/api/config":
                try:
                    text = Config(runner.config_path).dumps()
                except Exception as error:  # noqa: BLE001
                    self._json(500, {"ok": False, "error": str(error)})
                    return
                self._json(200, {"ok": True, "yaml": text})
                return
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                port = self.server.server_port
                allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
                host = self.headers.get("Host", "")
                if (host not in allowed or self.headers.get("Origin") != f"http://{host}"
                        or self.headers.get("X-Booker-Action") != "shutdown"):
                    self._json(403, {"ok": False, "error": "请从本机控制台退出程序"})
                    return
                with self.server.close_lock:
                    first = not runner.closing
                    runner.begin_close()
                try:
                    self._json(202, {"ok": True})
                finally:
                    if first:
                        # The response acknowledges the request. Stop serving after cleanup.
                        def finish():
                            try:
                                checker.close()
                                runner.close()
                            finally:
                                self.server.shutdown()
                        threading.Thread(target=finish, daemon=True).start()
                return
            if runner.closing:
                self._json(409, {"ok": False, "error": "控制台正在退出"})
                return
            if path == "/api/config":
                payload = self._body()
                text = str(payload.get("yaml") or "")
                try:
                    save_config_text(runner.config_path, text)
                except Exception as error:  # noqa: BLE001
                    self._json(400, {"ok": False, "error": str(error)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/api/login-check":
                payload = self._body()
                result = checker.check(
                    str(payload.get("username") or "").strip(),
                    str(payload.get("password") or ""),
                )
                self._json(200 if result.get("ok") else 400, result)
                return
            if path == "/api/start":
                payload = self._body()
                mode = str(payload.get("mode") or "grab")
                text = str(payload.get("yaml") or "")
                if text:
                    # 页面点「开始」时带上的配置：先校验落盘，再启动，避免跑的是旧配置
                    try:
                        save_config_text(runner.config_path, text)
                    except Exception as error:  # noqa: BLE001
                        self._json(400, {"ok": False, "error": str(error)})
                        return
                ok, message = runner.start(mode)
                self._json(200 if ok else 409, {"ok": ok, "message": message})
                return
            if path == "/api/stop":
                ok, message = runner.stop()
                self._json(200, {"ok": ok, "message": message})
                return
            self._send(404, b"not found", "text/plain")

    return Handler


def serve(config_path: str, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    runner = Runner(config_path)
    project_root = os.path.dirname(os.path.dirname(WEB_DIR))
    checker = LoginChecker(config_path, project_root)
    httpd = ThreadingHTTPServer((host, port), _handler_factory(runner, checker))
    httpd.close_lock = threading.Lock()
    url = f"http://{host}:{port}/"
    logger.info("配置控制台已启动：%s", url)
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("控制台已停止")
    finally:
        checker.close()
        runner.close()
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="羽毛球预约 · 本地控制台")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    serve(args.config, host=args.host, port=args.port, open_browser=not args.no_open)
