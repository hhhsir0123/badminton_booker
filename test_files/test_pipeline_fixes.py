"""P1 修复的回归测试：选场校验、启动窗口、跨页优先级、开始用页面配置。"""
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.court_booking import BookingManager  # noqa: E402
from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline  # noqa: E402


class FakeConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)


class ScriptDriver:
    def __init__(self, script_result=None, find_raises=True):
        self.script_result = script_result
        self.find_raises = find_raises
        self.current_url = "https://ggtypt.cumtb.edu.cn/venue/venue-reservation/3"
        self.scripts = []

    def execute_async_script(self, script, *args):
        return self.script_result

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if "Boolean(document.querySelector(arguments[0]))" in script:
            return False
        if isinstance(self.script_result, str):
            return self.script_result
        return self.script_result

    def find_element(self, *args, **kwargs):
        raise RuntimeError("no element")


def make_booking_manager(script_result):
    manager = BookingManager.__new__(BookingManager)
    manager.browser = SimpleNamespace(driver=ScriptDriver(script_result))
    manager.query = Mock()
    manager.query.ensure_visible.return_value = True
    manager.captcha_solver = None
    return manager


class SelectSlotsTest(unittest.TestCase):
    def test_empty_result_is_unavailable(self):
        manager = make_booking_manager({"ok": True, "results": []})
        result = manager.select_slots("训练馆3号", ["19:00-20:00"])
        self.assertEqual(result["status"], "unavailable")

    def test_selected_list_mismatch_is_rejected(self):
        manager = make_booking_manager(
            {"ok": True, "results": [{"ok": True, "changed": True, "slot": "19:00-20:00"}]}
        )
        # 页面底部「已选」里只有别的场地 → 必须判不一致
        manager._selected_matches = lambda court, slots: False
        result = manager.select_slots("训练馆3号", ["19:00-20:00"])
        self.assertEqual(result["status"], "mismatch")

    def test_selected_list_match(self):
        manager = make_booking_manager(
            {"ok": True, "results": [{"ok": True, "changed": True, "slot": "19:00-20:00"}]}
        )
        manager._selected_matches = lambda court, slots: True
        result = manager.select_slots("训练馆3号", ["19:00-20:00"])
        self.assertEqual(result["status"], "selected")


class CrossPageOrderTest(unittest.TestCase):
    def make_pipeline(self, pages):
        query = Mock()
        query.focus_date.return_value = True
        query.iter_pages.side_effect = lambda: iter(pages)
        query.free_slots.side_effect = lambda page: page

        pipeline = TicketGrabbingPipeline.__new__(TicketGrabbingPipeline)
        pipeline.logger = Mock()
        pipeline.summary = {}
        pipeline.config = FakeConfig({"runtime.grid_refresh": "auto"})
        pipeline.query_manager = query
        pipeline.captcha_solver = None
        return pipeline

    def test_first_choice_on_second_page_wins(self):
        first = {"训练馆4号": {"19:00-20:00": "free"}}
        second = {"训练馆3号": {"19:00-20:00": "free"}}
        pipeline = self.make_pipeline([first, second])
        choices = [
            ("训练馆3号", ("19:00", "20:00"), None),
            ("训练馆4号", ("19:00", "20:00"), None),
        ]
        found = pipeline._candidates_for("2026-11-21", datetime(2026, 11, 19, 7, 0, 0), choices)
        self.assertTrue(found)
        self.assertEqual(found[0][0], "训练馆3号")


class GrabWindowTest(unittest.TestCase):
    def test_late_start_still_queries(self):
        pipeline = TicketGrabbingPipeline.__new__(TicketGrabbingPipeline)
        pipeline.logger = Mock()
        pipeline.summary = {}
        pipeline._stopped = threading.Event()
        pipeline.config = FakeConfig({"runtime.probe_interval": 0, "runtime.grace_seconds": 4})
        clock = {"now": datetime(2026, 11, 19, 7, 0, 10)}

        def now():
            clock["now"] += timedelta(seconds=0.3)
            return clock["now"]

        pipeline._now = now
        pipeline.query_manager = Mock()
        pipeline.query_manager.probe.return_value = {"status": 200, "length": 400}
        pipeline.query_manager.focus_date.return_value = True
        pipeline.query_manager.iter_pages.return_value = iter([])
        pipeline.query_manager.snapshot.return_value = None
        pipeline.query_manager.free_slots.return_value = {}
        pipeline._read_availability = Mock(return_value={})
        pipeline._clear_captcha = lambda: True

        found = pipeline._grab("2026-11-21", datetime(2026, 11, 19, 7, 0, 0), [])
        self.assertEqual(found, [])
        # 启动已经晚了，也至少查一次，不能 0 次查询就判「没抢到」
        self.assertGreaterEqual(pipeline.summary.get("probe_queries", 0), 1)


if __name__ == "__main__":
    unittest.main()

class StaticGuardTest(unittest.TestCase):
    """防止「构造函数参数对不上」「同步脚本里用 done」这类只在真机才炸的问题。"""

    def test_pipeline_passes_only_supported_solver_kwargs(self):
        import inspect

        from src.pipeline.base_pipeline import BasePipeline
        from src.utils.captcha_solver import CaptchaSolver

        params = inspect.signature(CaptchaSolver.__init__).parameters
        source = inspect.getsource(BasePipeline.start)
        for name in ("max_attempts", "min_length", "max_length", "ensemble"):
            self.assertIn(name, params, name)
        self.assertNotIn("slider_wait", source)
        self.assertNotIn("slider_wait", params)

    def test_pagination_script_is_synchronous(self):
        import inspect

        from src.core.court_query import QueryManager

        source = inspect.getsource(QueryManager._turn)
        # 分页箭头是用 execute_script 调的，脚本里不能出现 async 的 done 回调
        self.assertNotIn("done", source)

