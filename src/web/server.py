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
        self._lock = threading.Lock()
        handler = _LogBuffer(self.logs, self.core)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)

    # ------------------------------------------------------------- 状态

    def status(self) -> dict:
        alive = bool(self.thread and self.thread.is_alive())
        message = self.message
        pipeline = self.pipeline
        if pipeline is not None:
            summary = getattr(pipeline, "summary", None) or {}
            message = summary.get("message") or message
        return {
            "state": "running" if alive else self.state,
            "mode": self.mode,
            "message": message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": int(time.time() - self.started_at) if self.started_at and alive else None,
            "logs": list(self.logs)[-120:],
            "core": list(self.core)[-80:],
        }

    # ------------------------------------------------------------- 启停

    def start(self, mode: str) -> tuple[bool, str]:
        with self._lock:
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
        pipeline = self.pipeline
        if pipeline is None:
            self.state = "idle"
            self.message = "没有在运行"
            return False, "没有在运行"
        self._stop_requested = True
        try:
            pipeline.stop()
        except Exception as error:  # noqa: BLE001
            logger.warning("停止失败: %s", error)
        self.message = "正在停止…"
        return True, "正在停止"

    def _run(self, mode: str):
        try:
            if mode == "poll":
                from ..pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline

                pipeline = PeriodicScanningPipeline(self.config_path)
                self.pipeline = pipeline
                ok = pipeline.run()
            else:
                from ..pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline

                pipeline = TicketGrabbingPipeline(self.config_path)
                self.pipeline = pipeline
                ok = pipeline.run()
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
            self.finished_at = time.time()
            self.pipeline = None
            self.thread = None


def _handler_factory(runner: Runner):
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
            if path == "/api/config":
                payload = self._body()
                text = str(payload.get("yaml") or "")
                try:
                    import yaml as _yaml

                    from ..config import DEFAULTS, deep_merge

                    parsed = _yaml.safe_load(text) or {}
                    if not isinstance(parsed, dict):
                        raise ValueError("配置顶层必须是字典")
                    config = Config(runner.config_path)
                    config.config = deep_merge(DEFAULTS, parsed)
                    errors = validate_for_run(config.config)
                    if errors:
                        self._json(400, {"ok": False, "error": "；".join(errors)})
                        return
                    config.save()
                except Exception as error:  # noqa: BLE001
                    self._json(400, {"ok": False, "error": str(error)})
                    return
                self._json(200, {"ok": True})
                return
            if path == "/api/start":
                mode = str(self._body().get("mode") or "grab")
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
    httpd = ThreadingHTTPServer((host, port), _handler_factory(runner))
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
        runner.stop()
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
