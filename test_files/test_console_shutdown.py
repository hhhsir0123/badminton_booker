"""本地控制台退出测试。所有预约任务都用替身。"""
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

from src.web.server import Runner, serve, ThreadingHTTPServer


class ConsoleShutdownTests(unittest.TestCase):
    def test_stop_while_constructing_pipeline(self):
        constructing = threading.Event()
        release = threading.Event()
        pipeline = Mock(browser=None)

        def construct(*args):
            constructing.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return pipeline

        with tempfile.TemporaryDirectory() as root, patch(
            "src.pipeline.ticket_grabbing_pipeline.TicketGrabbingPipeline", side_effect=construct
        ), patch("src.web.server.validate_for_run", return_value=[]):
            runner = Runner(str(Path(root) / "config.yaml"))
            self.assertTrue(runner.start("grab")[0])
            try:
                self.assertTrue(constructing.wait(5))
                worker = runner.thread
                self.assertTrue(runner.stop()[0])
            finally:
                release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            pipeline.run.assert_not_called()
            pipeline.cleanup.assert_called_once()

    def test_local_shutdown_exits_server(self):
        ready = threading.Event()
        address = []

        def create(*args):
            server = ThreadingHTTPServer(*args)
            address.append(server.server_address)
            ready.set()
            return server

        with tempfile.TemporaryDirectory() as root, patch(
            "src.web.server.ThreadingHTTPServer", side_effect=create
        ):
            worker = threading.Thread(target=serve, args=(str(Path(root) / "config.yaml"),),
                                      kwargs={"port": 0, "open_browser": False}, daemon=True)
            worker.start()
            self.assertTrue(ready.wait(5))
            host, port = address[0]
            client = HTTPConnection(host, port, timeout=3)
            try:
                client.request("POST", "/api/shutdown")
                response = client.getresponse()
                response.read()
                self.assertEqual(403, response.status)
                client.request("POST", "/api/shutdown", headers={
                    "Origin": f"http://{host}:{port}", "X-Booker-Action": "shutdown"
                })
                response = client.getresponse()
                self.assertEqual(202, response.status)
                self.assertTrue(json.load(response)["ok"])
            finally:
                client.close()
            worker.join(5)
            self.assertFalse(worker.is_alive())
