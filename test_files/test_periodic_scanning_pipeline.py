"""空场轮询流程测试：不启动浏览器，用 mock 替换查询与下单。"""
import os
import sys
import threading
import unittest
from datetime import date, timedelta
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
    pipeline.polling_time_range = "19:00-21:00"
    pipeline.polling_hours = 1
    pipeline.polling_time_slots = []
    pipeline.polling_courts = []
    pipeline.polling_date = ""
    pipeline.polling_date_offsets = [0]
    pipeline.is_auto_booking = False
    pipeline.partner_name = ""
    pipeline.venue_type = "羽毛球"
    pipeline.query_manager = Mock()
    pipeline.query_manager.query_available_courts.return_value = {}
    pipeline.query_manager.last_query_status = {"ok": True}
    pipeline.query_manager.open_venue_site.return_value = True
    pipeline.query_manager.snapshot.return_value = {"labels": ["时间段", "训练馆3号"]}

    def scan(day):
        courts = pipeline.query_manager.query_available_courts.return_value
        return {court: {slot: slot.replace("-", "～") for slot in slots}
                for court, slots in courts.items()}

    pipeline.query_manager.scan.side_effect = scan
    pipeline.booking_manager = Mock()
    # 默认当作「已支付」：不真的等 15 分钟，支付等待有专门的用例
    pipeline._await_payment = Mock(return_value="paid")
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
        query.scan.side_effect = [
            {"训练馆3号": {"19:00-20:00": "19:00～20:00"}},
            {"训练馆3号": {"20:00-21:00": "20:00～21:00"},
             "训练馆4号": {"19:00-20:00": "19:00～20:00"}},
        ]
        query.last_query_status = {"ok": True}
        pipeline = make_pipeline(polling_date_offsets=[0, 1], query_manager=query)
        result = pipeline._query_available_courts()
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        # 不同日期必须分开，不能合并成「同一场地连续两小时」
        self.assertEqual(result[today], {"训练馆3号": {"19:00-20:00": "19:00～20:00"}})
        self.assertEqual(result[tomorrow], {
            "训练馆3号": {"20:00-21:00": "20:00～21:00"},
            "训练馆4号": {"19:00-20:00": "19:00～20:00"},
        })
        self.assertEqual(query.scan.call_count, 2)

    def test_tolerates_failure(self):
        query = Mock()
        query.scan.side_effect = RuntimeError("boom")
        pipeline = make_pipeline(query_manager=query)
        self.assertEqual(pipeline._query_available_courts(), {})
        pipeline.logger.error.assert_called()


class ScanTest(unittest.TestCase):
    def test_empty_scan_counts_as_success(self):
        # 读表成功、只是没有空场：不能当成失败去退避/重登录
        pipeline = make_pipeline()
        self.assertTrue(pipeline._perform_scan())

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
        # 轮询要在提交时现场解验证码，所以不能再传 confirm_terms=True
        self.assertFalse(pipeline.booking_manager.book_court.call_args.kwargs["confirm_terms"])
        self.assertEqual(pipeline.booking_manager.book_court.call_args.kwargs["time_slots"], ["19:00～20:00"])

    def test_only_watched_courts_are_booked(self):
        pipeline = make_pipeline(
            is_auto_booking=True,
            polling_time_slots=["19:00-20:00"],
            polling_courts=["气膜馆5号"],
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"],
            "气膜馆5号": ["19:00-20:00"],
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED

        pipeline._perform_scan()

        self.assertEqual(
            pipeline.booking_manager.book_court.call_args.kwargs["court_name"], "气膜馆5号"
        )

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

    def test_stops_after_successful_booking(self):
        pipeline = make_pipeline(is_auto_booking=True, polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED
        pipeline.cleanup = Mock()
        with patch.object(pipeline, "start_pipeline", return_value=True):
            self.assertTrue(pipeline.run())
        # 抢到就结束，不能下一轮又下一单
        self.assertEqual(pipeline.summary["rounds"], 1)
        pipeline.cleanup.assert_called_once()

    def test_run_ends_only_after_payment(self):
        pipeline = make_pipeline(is_auto_booking=True, polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED
        pipeline.cleanup = Mock()
        with patch.object(pipeline, "start_pipeline", return_value=True):
            self.assertTrue(pipeline.run())
        self.assertTrue(pipeline._paid)
        self.assertEqual(pipeline.summary["state"], "done")
        pipeline.cleanup.assert_called_once()

    def test_loop_stops_after_one_scan(self):
        pipeline = make_pipeline()
        pipeline._perform_scan = Mock(side_effect=lambda: pipeline.stop())
        pipeline.cleanup = Mock()
        with patch.object(pipeline, "start_pipeline", return_value=True):
            self.assertTrue(pipeline.run())
        pipeline._perform_scan.assert_called_once()
        pipeline.cleanup.assert_called_once()
        self.assertEqual(pipeline.summary["state"], "stopped")




class OnsiteCaptchaBookingTest(unittest.TestCase):
    """查到空场后走平台自己的提交流程：点提交 → 现场解验证码 → 看接口回执。"""

    def make(self, **overrides):
        pipeline = make_pipeline(
            is_auto_booking=True, polling_time_slots=["19:00-20:00"], **overrides
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.select_segments.return_value = {"status": "selected"}
        return pipeline

    def make_flow(self, **overrides):
        flow = Mock()
        flow.click_submit_button.return_value = True
        flow.order_mark.return_value = 0
        flow.has_captcha.return_value = True
        flow.solve_submit_captcha.return_value = True
        # 1 秒内的探测没有回执（还在等验证码），解完之后才拿到成功回执
        flow.wait_order_response.side_effect = lambda mark, timeout=0: (
            {"code": 200, "data": "TRADE123", "msg": "操作成功"} if timeout > 1.5 else None
        )
        flow.order_response.return_value = {"code": 200, "data": "TRADE123", "msg": "操作成功"}
        flow.wait_outcome.return_value = "created"
        for key, value in overrides.items():
            setattr(flow, key, value)
        return flow

    def test_solves_captcha_then_submits(self):
        pipeline = self.make()
        flow = self.make_flow()
        pipeline.submit_captcha = flow

        pipeline._perform_scan()

        flow.click_submit_button.assert_called_once()
        flow.solve_submit_captcha.assert_called_once()
        pipeline.booking_manager.book_court.assert_not_called()
        self.assertEqual(pipeline.summary["booked"][0]["segments"][0]["court"], "训练馆3号")

    def test_captcha_failure_submits_nothing(self):
        pipeline = self.make()
        flow = self.make_flow(solve_submit_captcha=Mock(return_value=False))
        pipeline.submit_captcha = flow

        pipeline._perform_scan()

        flow.wait_outcome.assert_not_called()
        pipeline.booking_manager.book_court.assert_not_called()
        self.assertNotIn("booked", pipeline.summary)

    def test_three_captcha_failures_halt_polling(self):
        pipeline = self.make()
        pipeline.submit_captcha = self.make_flow(solve_submit_captcha=Mock(return_value=False))

        for _ in range(3):
            pipeline._perform_scan()

        self.assertTrue(pipeline._halt)
        self.assertIn("验证码", pipeline.summary["message"])

    def test_success_resets_captcha_failure_counter(self):
        pipeline = self.make()
        flow = self.make_flow(solve_submit_captcha=Mock(return_value=False))
        pipeline.submit_captcha = flow
        pipeline._perform_scan()
        pipeline._perform_scan()
        self.assertEqual(pipeline._captcha_failures, 2)

        flow.solve_submit_captcha = Mock(return_value=True)
        pipeline._perform_scan()

        self.assertEqual(pipeline._captcha_failures, 0)
        self.assertFalse(getattr(pipeline, "_halt", False))

    def test_halted_scan_stops_before_next_day(self):
        pipeline = self.make()
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"], "训练馆4号": ["19:00-20:00"],
        }
        pipeline.submit_captcha = self.make_flow(solve_submit_captcha=Mock(return_value=False))
        pipeline._perform_scan()
        pipeline._perform_scan()
        pipeline._perform_scan()
        # 第 3 次失败后应当直接停手，不再对后面的日期继续下单
        self.assertTrue(pipeline._halt)
        self.assertEqual(pipeline.submit_captcha.click_submit_button.call_count, 3)

    def test_rejected_tries_next_candidate(self):
        pipeline = make_pipeline(is_auto_booking=True, polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"],
            "训练馆4号": ["19:00-20:00"],
        }
        pipeline.booking_manager.select_segments.return_value = {"status": "selected"}
        flow = self.make_flow()
        flow.wait_order_response.side_effect = [
            {"code": 250, "msg": "该场次已被预约"},
            {"code": 200, "data": "TRADE456"},
        ]
        pipeline.submit_captcha = flow

        pipeline._perform_scan()

        self.assertEqual(flow.click_submit_button.call_count, 2)
        self.assertEqual(pipeline.summary["booked"][0]["segments"][0]["court"], "训练馆4号")

    def test_unknown_outcome_stops_and_notifies_failure(self):
        notifier = Mock(enabled=True)
        pipeline = self.make(feishu_notifier=notifier)
        pipeline.config.data["feishu.notify_failure"] = True
        # 接口回了 200 但没带订单数据，页面也没提示 → 判 UNKNOWN
        flow = self.make_flow(
            wait_order_response=Mock(return_value=None),
            order_response=Mock(return_value={"code": 200, "msg": "操作成功"}),
            wait_outcome=Mock(return_value="unknown"),
        )
        pipeline.submit_captcha = flow

        pipeline._perform_scan()

        notifier.send_failure.assert_called_once()
        self.assertNotIn("booked", pipeline.summary)


class WatchedCourtsConfigTest(unittest.TestCase):
    def test_all_courts_token_means_unlimited(self):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
            handle.write("polling:\n  polling_courts: ['全部场地']\n")
            path = handle.name
        try:
            pipeline = PeriodicScanningPipeline(path)
            self.assertEqual(pipeline.polling_courts, [])
        finally:
            os.unlink(path)


class ContinuousWindowTest(unittest.TestCase):
    """时间段 + 连续小时数：优先同场地，凑不齐才跨场地。"""

    def test_two_hours_prefers_same_court(self):
        pipeline = make_pipeline(polling_hours=2, polling_time_range="15:00-18:00")
        inventory = {
            "气膜馆5号": {"15:00-16:00": "15:00～16:00", "16:00-17:00": "16:00～17:00"},
            "气膜馆6号": {"15:00-16:00": "15:00～16:00", "17:00-18:00": "17:00～18:00"},
        }
        windows = pipeline._find_windows(inventory)
        self.assertEqual(
            windows[0],
            [("气膜馆5号", "15:00-16:00", "15:00～16:00"),
             ("气膜馆5号", "16:00-17:00", "16:00～17:00")],
        )

    def test_two_hours_can_cross_courts(self):
        pipeline = make_pipeline(polling_hours=2, polling_time_range="15:00-18:00")
        inventory = {
            "气膜馆5号": {"15:00-16:00": "15:00～16:00"},
            "气膜馆6号": {"16:00-17:00": "16:00～17:00"},
        }
        windows = pipeline._find_windows(inventory)
        self.assertEqual(
            windows,
            [[("气膜馆5号", "15:00-16:00", "15:00～16:00"),
              ("气膜馆6号", "16:00-17:00", "16:00～17:00")]],
        )

    def test_one_hour_window(self):
        pipeline = make_pipeline(polling_hours=1, polling_time_range="15:00-17:00")
        inventory = {"气膜馆5号": {"16:00-17:00": "16:00～17:00"}}
        self.assertEqual(
            pipeline._find_windows(inventory),
            [[("气膜馆5号", "16:00-17:00", "16:00～17:00")]],
        )

    def test_watched_courts_limit_windows(self):
        pipeline = make_pipeline(
            polling_hours=1, polling_time_range="15:00-17:00", polling_courts=["气膜馆6号"]
        )
        inventory = {
            "气膜馆5号": {"15:00-16:00": "15:00～16:00"},
            "气膜馆6号": {"16:00-17:00": "16:00～17:00"},
        }
        self.assertEqual([window[0][0] for window in pipeline._find_windows(inventory)], ["气膜馆6号"])

    def test_cross_court_booking_submits_once(self):
        pipeline = make_pipeline(
            is_auto_booking=True, polling_hours=2, polling_time_range="15:00-18:00"
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "气膜馆5号": ["15:00-16:00"],
            "气膜馆6号": ["16:00-17:00"],
        }
        pipeline.booking_manager.select_segments.return_value = {"status": "selected"}
        flow = Mock()
        flow.click_submit_button.return_value = True
        flow.order_mark.return_value = 0
        flow.has_captcha.return_value = True
        flow.solve_submit_captcha.return_value = True
        flow.wait_order_response.side_effect = lambda mark, timeout=0: (
            {"code": 200, "data": "TRADE123"} if timeout > 1.5 else None
        )
        flow.order_response.return_value = {"code": 200, "data": "TRADE123"}
        pipeline.submit_captcha = flow

        pipeline._perform_scan()

        # 两个格子一次选中、只提交一次 → 一单两段
        segments = pipeline.booking_manager.select_segments.call_args.args[0]
        self.assertEqual([court for court, _ in segments], ["气膜馆5号", "气膜馆6号"])
        flow.click_submit_button.assert_called_once()
        booking = pipeline.summary["booked"][0]
        self.assertEqual(len(booking["segments"]), 2)
        self.assertEqual(booking["segments"][1]["court"], "气膜馆6号")


class PaymentWaitTest(unittest.TestCase):
    """抢到后守着订单：每 60 秒查一次，付了结束，15 分钟没付就重新抢。"""

    BOOKING = {"day": "2026-11-22", "court": "训练馆3号", "slots": ["19:00-20:00"]}

    @staticmethod
    def real_pipeline(**overrides):
        pipeline = make_pipeline(**overrides)
        # make_pipeline 把 _await_payment 换成了 Mock，这里恢复真实实现
        pipeline._await_payment = PeriodicScanningPipeline._await_payment.__get__(pipeline)
        return pipeline

    def test_payment_state_mapping(self):
        pipeline = make_pipeline()
        cases = [
            ([3], "unpaid"),
            ([4], "paid"),
            ([1], "expired"),
            ([2], "expired"),
            ([None], "unknown"),
            ([3, 4], "unpaid"),
        ]
        for statuses, expected in cases:
            pipeline.query_manager.slot_status = Mock(side_effect=statuses)
            booking = dict(self.BOOKING, slots=[f"19:0{i}-20:0{i}" for i in range(len(statuses))])
            self.assertEqual(pipeline._payment_state(booking), expected, statuses)

    def test_paid_ends_polling(self):
        pipeline = self.real_pipeline()
        pipeline.payment_check_seconds = 0
        pipeline._payment_state = Mock(return_value="paid")
        notifier = Mock(enabled=True)
        pipeline.feishu_notifier = notifier

        self.assertEqual(pipeline._await_payment(dict(self.BOOKING)), "paid")
        notifier.send_text.assert_called_once()

    def test_unpaid_reminds_then_expires(self):
        pipeline = self.real_pipeline()
        pipeline.payment_window_seconds = 0.15
        pipeline.payment_check_seconds = 0.01
        pipeline._payment_state = Mock(return_value="unpaid")
        notifier = Mock(enabled=True)
        pipeline.feishu_notifier = notifier

        self.assertEqual(pipeline._await_payment(dict(self.BOOKING)), "expired")
        self.assertGreaterEqual(notifier.send_text.call_count, 1)

    def test_stop_interrupts_payment_wait(self):
        pipeline = self.real_pipeline()
        pipeline.payment_window_seconds = 60
        pipeline.payment_check_seconds = 0
        pipeline._payment_state = Mock(return_value="unpaid")
        pipeline.feishu_notifier = Mock(enabled=True)

        def stop_on_remind(*args, **kwargs):
            pipeline.stop()

        pipeline._notify_payment_reminder = Mock(side_effect=stop_on_remind)
        self.assertEqual(pipeline._await_payment(dict(self.BOOKING)), "stopped")

    def test_expired_order_releases_and_regrabs(self):
        pipeline = make_pipeline(is_auto_booking=True, polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED
        pipeline._await_payment = Mock(return_value="expired")

        pipeline._perform_scan()

        self.assertFalse(getattr(pipeline, "_paid", False))
        self.assertEqual(pipeline._booked_list(), [])
        self.assertTrue(pipeline._retry_soon)
        self.assertNotIn("booked", pipeline.summary)

    def test_paid_scan_marks_booked(self):
        pipeline = make_pipeline(is_auto_booking=True, polling_time_slots=["19:00-20:00"])
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline.booking_manager.book_court.return_value = BookingOutcome.CREATED

        pipeline._perform_scan()

        self.assertTrue(pipeline._paid)
        self.assertEqual(pipeline.summary["booked"][0]["segments"][0]["court"], "训练馆3号")


class BackoffTest(unittest.TestCase):
    def test_backoff_grows_then_caps_and_resets(self):
        pipeline = make_pipeline()
        pipeline.scan_interval = 60
        pipeline._recover = Mock(return_value=False)
        waits = [pipeline._next_wait(False) for _ in range(5)]
        self.assertEqual(waits, [60, 120, 240, 480, 600])
        self.assertEqual(pipeline._next_wait(True), 60)


class NotificationDedupTest(unittest.TestCase):
    """同一批空场只推一次，消失后再出现要重新推。"""

    def test_same_availability_notified_once(self):
        notifier = Mock(enabled=True)
        pipeline = make_pipeline(
            polling_time_slots=["19:00-20:00"], feishu_notifier=notifier
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }

        pipeline._perform_scan()
        pipeline._perform_scan()

        notifier.send_availability.assert_called_once()

    def test_renotifies_after_availability_returns(self):
        notifier = Mock(enabled=True)
        pipeline = make_pipeline(
            polling_time_slots=["19:00-20:00"], feishu_notifier=notifier
        )
        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()

        pipeline.query_manager.query_available_courts.return_value = {}
        pipeline._perform_scan()

        pipeline.query_manager.query_available_courts.return_value = {
            "训练馆3号": ["19:00-20:00"]
        }
        pipeline._perform_scan()

        self.assertEqual(notifier.send_availability.call_count, 2)


if __name__ == "__main__":
    unittest.main()


class HeartbeatTest(unittest.TestCase):
    """每小时一条飞书心跳，证明轮询还活着。"""

    def make(self, period=3600):
        from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline

        pipeline = object.__new__(PeriodicScanningPipeline)
        pipeline.config = Mock()
        pipeline.config.get.side_effect = lambda key, default=None: {
            "polling.heartbeat_seconds": period,
            "polling.polling_time_range": "14:00-21:00",
            "polling.polling_hours": 2,
            "polling.polling_courts": ["全部场地"],
        }.get(key, default)
        pipeline.summary = {"last_seconds": 4.2}
        pipeline._failures = 0
        pipeline._dates = lambda: ["2026-11-28"]
        pipeline.logger = Mock()
        pipeline.feishu_notifier = Mock()
        pipeline.feishu_notifier.enabled = True
        pipeline.feishu_notifier.send_text.return_value = True
        return pipeline

    def test_first_call_only_starts_timer(self):
        pipeline = self.make()
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=1000.0):
            pipeline._maybe_heartbeat(1, True)
        pipeline.feishu_notifier.send_text.assert_not_called()

    def test_sends_after_period_and_not_before(self):
        pipeline = self.make(period=3600)
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=1000.0):
            pipeline._maybe_heartbeat(1, True)
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=2000.0):
            pipeline._maybe_heartbeat(2, True)
        pipeline.feishu_notifier.send_text.assert_not_called()
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=4700.0):
            pipeline._maybe_heartbeat(70, True)
        pipeline.feishu_notifier.send_text.assert_called_once()
        message = pipeline.feishu_notifier.send_text.call_args[0][0]
        self.assertIn("轮询心跳", message)
        self.assertIn("2026-11-28", message)
        self.assertIn("70", message)

    def test_disabled_when_zero(self):
        pipeline = self.make(period=0)
        pipeline._last_heartbeat = 0.0
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=99999.0):
            pipeline._maybe_heartbeat(1, True)
        pipeline.feishu_notifier.send_text.assert_not_called()

    def test_skips_when_notifier_disabled(self):
        pipeline = self.make()
        pipeline.feishu_notifier.enabled = False
        pipeline._last_heartbeat = 0.0
        with patch("src.pipeline.periodic_scanning_pipeline.time.monotonic", return_value=99999.0):
            pipeline._maybe_heartbeat(1, True)
        pipeline.feishu_notifier.send_text.assert_not_called()
