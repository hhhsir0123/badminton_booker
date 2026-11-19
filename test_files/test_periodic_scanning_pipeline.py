"""空场轮询流程测试：不启动浏览器，用 mock 替换查询与下单。"""
import os
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.booking_result import BookingOutcome
from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline


class FakeConfig:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)


def make_pipeline(**overrides):
    pipeline = PeriodicScanningPipeline.__new__(PeriodicScanningPipeline)
    pipeline._stopped = threading.Event()
    pipeline.logger = Mock()
    pipeline.summary = {}
    pipeline.config = FakeConfig(
        {"booking.venue_type": "羽毛球", "user.partner_name": ""}
    )
    pipeline.scan_interval = 0
    pipeline.polling_time_slots = []
    pipeline.polling_date = ""
    pipeline.polling_date_offsets = [0]
    pipeline.is_auto_booking = False
    pipeline.partner_name = ""
    pipeline.venue_type = "羽毛球"
    pipeline.query_manager = Mock()
    pipeline.query_manager.query_available_courts.return_value = {}
    pipeline.booking_manager = Mock()
    for key, value in overrides.items():
        setattr(pipeline, key, value)
    return pipeline


class DatesTest(unittest.TestCase):
    def test_explicit_date(self):
        pipeline = make_pipeline(polling_date="2026-12-06")
        self.assertEqual(pipeline._dates(), ["2026-12-06"])

    def test_offsets(self):
        from datetime import date, timedelta

        pipeline = make_pipeline(polling_date_offsets=[0, 1])
        today = date.today()
        self.assertEqual(
            pipeline._dates(),
            [today.isoformat(), (today + timedelta(days=1)).isoformat()],
        )


class QueryTest(unittest.TestCase):
    def test_merge_multiple_days(self):
        query = Mock()
        query.query_available_courts.side_effect = [
            {"训练馆3号": ["19:00-20:00"]},
            {"训练馆3号": ["20:00-21:00"], "训练馆4号": ["19:00-20:00"]},
        ]
        pipeline = make_pipeline(polling_date_offsets=[0, 1], query_manager=query)
        result = pipeline._query_available_courts()
        self.assertEqual(result["训练馆3号"], ["19:00-20:00", "20:00-21:00"])
        self.assertEqual(result["训练馆4号"], ["19:00-20:00"])
        self.assertEqual(query.query_available_courts.call_count, 2)

    def test_tolerates_failure(self):
        query = Mock()
        query.query_available_courts.side_effect = RuntimeError("boom")
        pipeline = make_pipeline(query_manager=query)
        self.assertEqual(pipeline._query_available_courts(), {})
        pipeline.logger.error.assert_called()


class ScanTest(unittest.TestCase):
    def test_nothing_available(self):
        pipeline = make_pipeline()
        pipeline._perform_scan()
        pipeline.booking_manager.book_court.assert_not_called()

    def test_auto_booking(self):
        pipeline = make_pipeline(
            is_auto_booking=True,
            polling_time_slots=["19:00-20:00"],
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED
        pipeline._perform_scan()
        pipeline.booking_manager.book_court.assert_called_once()

    def test_availability_only(self):
        pipeline = make_pipeline(polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()
        pipeline.booking_manager.book_court.assert_not_called()

    def test_unknown_outcome_stops(self):
        pipeline = make_pipeline(
            is_auto_booking=True,
            polling_time_slots=["19:00-20:00"],
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.UNKNOWN
        pipeline._perform_scan()
        self.assertEqual(pipeline.booking_manager.book_court.call_count, 1)

    def test_no_notifier_configured(self):
        pipeline = make_pipeline(polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()

    def test_feishu_availability(self):
        from unittest.mock import Mock

        notifier = Mock(enabled=True)
        pipeline = make_pipeline(
            polling_time_slots=["19:00-20:00"], feishu_notifier=notifier
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()
        notifier.send_availability.assert_called_once()
        args = notifier.send_availability.call_args[0]
        self.assertEqual(args[1], {"训练馆3号": ["19:00-20:00"]})

    def test_feishu_success(self):
        from unittest.mock import Mock

        notifier = Mock(enabled=True)
        pipeline = make_pipeline(
            is_auto_booking=True,
            polling_time_slots=["19:00-20:00"],
            feishu_notifier=notifier,
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED
        pipeline._perform_scan()
        notifier.send_booking_success.assert_called_once()

    def test_feishu_can_be_silenced(self):
        from unittest.mock import Mock

        notifier = Mock(enabled=True)
        pipeline = make_pipeline(
            polling_time_slots=["19:00-20:00"], feishu_notifier=notifier
        )
        pipeline.config.data["feishu.notify_availability"] = False
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()
        notifier.send_availability.assert_not_called()


class RunTest(unittest.TestCase):
    def test_start_failure(self):
        pipeline = make_pipeline()
        with patch.object(pipeline, "start_pipeline", return_value=False):
            self.assertFalse(pipeline.run())

    def test_loop_stops_after_one_scan(self):
        pipeline = make_pipeline()
        pipeline._perform_scan = Mock(side_effect=lambda: pipeline.stop())
        pipeline.cleanup = Mock()
        with patch.object(pipeline, "start_pipeline", return_value=True):
            self.assertTrue(pipeline.run())
        pipeline._perform_scan.assert_called_once()
        pipeline.cleanup.assert_called_once()
        self.assertEqual(pipeline.summary["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
