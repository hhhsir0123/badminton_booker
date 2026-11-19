"""订单结果判定与「结果未知时不重提」的测试。"""
import os
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.booking_result import BookingOutcome  # noqa: E402
from src.core.court_booking import SUCCESS_HINTS, BookingManager  # noqa: E402
from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline  # noqa: E402


class FakeDriver:
    def __init__(self, url="https://ggtypt.cumtb.edu.cn/venue/venue-reservation/3"):
        self.current_url = url


class FakeConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)


def make_manager(url=None):
    from types import SimpleNamespace

    manager = BookingManager.__new__(BookingManager)
    manager.browser = SimpleNamespace(driver=FakeDriver(url) if url else FakeDriver())
    return manager


class VerdictTest(unittest.TestCase):
    def test_success_hints_must_not_contain_xia_dan(self):
        # 「下单失败」里也有「下单」，不能作为成功依据
        self.assertNotIn("下单", SUCCESS_HINTS)

    def test_xia_dan_failed_toast_is_rejected(self):
        payload = {"ok": False, "reason": "toast", "message": "下单失败，请稍后重试"}
        self.assertIs(make_manager()._verdict(payload), BookingOutcome.REJECTED)

    def test_success_toast_is_created(self):
        payload = {"ok": True, "reason": "toast", "message": "提交预约成功"}
        self.assertIs(make_manager()._verdict(payload), BookingOutcome.CREATED)

    def test_navigated_to_login_is_unknown(self):
        payload = {"ok": True, "reason": "navigated",
                   "url": "https://ggtypt.cumtb.edu.cn/venue/login"}
        self.assertIs(make_manager()._verdict(payload), BookingOutcome.UNKNOWN)

    def test_navigated_to_order_is_created(self):
        payload = {"ok": True, "reason": "navigated",
                   "url": "https://ggtypt.cumtb.edu.cn/venue/orders"}
        self.assertIs(make_manager()._verdict(payload), BookingOutcome.CREATED)

    def test_timeout_is_unknown(self):
        payload = {"ok": False, "reason": "timeout", "message": ""}
        self.assertIs(make_manager()._verdict(payload), BookingOutcome.UNKNOWN)


class PresolvedSubmitTest(unittest.TestCase):
    def make_pipeline(self, selection, submitted, success):
        pipeline = TicketGrabbingPipeline.__new__(TicketGrabbingPipeline)
        pipeline.logger = Mock()
        pipeline.summary = {}
        pipeline.config = FakeConfig({"runtime.submit_timeout_ms": 500})
        pipeline.booking_manager = Mock()
        pipeline.booking_manager.select_slots.return_value = selection
        pipeline.submit_captcha = Mock()
        pipeline.submit_captcha.submit_selected.return_value = submitted
        pipeline.submit_captcha.wait_success.return_value = success
        return pipeline

    def test_ok(self):
        pipeline = self.make_pipeline({"status": "selected"}, True, True)
        self.assertEqual(pipeline._submit_presolved("训练馆3号", ["19:00-20:00"]), "ok")

    def test_selection_failed_is_rejected(self):
        pipeline = self.make_pipeline({"status": "unavailable"}, True, True)
        self.assertEqual(pipeline._submit_presolved("训练馆3号", ["19:00-20:00"]), "rejected")

    def test_not_submitted_is_unknown(self):
        pipeline = self.make_pipeline({"status": "selected"}, False, True)
        self.assertEqual(pipeline._submit_presolved("训练馆3号", ["19:00-20:00"]), "unknown")

    def test_timeout_is_unknown_not_retry(self):
        pipeline = self.make_pipeline({"status": "selected"}, True, False)
        self.assertEqual(pipeline._submit_presolved("训练馆3号", ["19:00-20:00"]), "unknown")


if __name__ == "__main__":
    unittest.main()
