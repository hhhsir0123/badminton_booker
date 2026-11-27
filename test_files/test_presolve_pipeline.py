"""预解组件接入、实际结果验证和失败兜底；全部使用替身，不访问平台。"""
import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.booking_result import BookingOutcome
from src.core.submit_captcha import PreSolvedCaptcha
from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline
from src.utils.click_word_solver import ClickWordSolver


class Config:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def pipeline(**config):
    result = TicketGrabbingPipeline.__new__(TicketGrabbingPipeline)
    result.config = Config(**config)
    result.logger = Mock()
    result.summary = {}
    result._now = lambda: datetime.now(timezone.utc)
    result.browser = SimpleNamespace(driver=Mock())
    result.submit_captcha = None
    return result


class PresolvePipelineTest(unittest.TestCase):
    def test_start_initializes_component_and_warms_solver(self):
        job = pipeline()
        with patch("src.pipeline.base_pipeline.BasePipeline.start", return_value=True), \
                patch("src.utils.click_word_solver.ClickWordSolver") as solver:
            solver.return_value.warmup.return_value = True
            self.assertTrue(job.start())
            self.assertIsNotNone(job.submit_captcha)
            self.assertIs(job.submit_captcha.driver, job.browser.driver)
            solver.return_value.warmup.assert_called_once()
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "component_ready")

    def test_start_unavailable_model_reports_fallback(self):
        job = pipeline()
        with patch("src.pipeline.base_pipeline.BasePipeline.start", return_value=True), \
                patch("src.utils.click_word_solver.ClickWordSolver") as solver:
            solver.return_value.warmup.return_value = False
            self.assertTrue(job.start())
        self.assertIsNone(job.submit_captcha)
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "fallback")
        self.assertIn("现场验证", job.summary["captcha_pre_solve"]["message"])

    def test_configured_provider_and_no_local_fallback(self):
        job = pipeline(**{"captcha.provider": "ttshitu", "captcha.fallback_to_local": False})
        with patch("src.pipeline.base_pipeline.BasePipeline.start", return_value=True), \
                patch("src.utils.click_word_solver.ClickWordSolver") as solver, \
                patch("src.utils.ttshitu_provider.TTShituProvider.from_config") as provider:
            self.assertTrue(job.start())
            provider.return_value.check_credentials.assert_called_once()
            solver.assert_called_once_with(provider=provider.return_value, fallback_to_local=False)
            solver.return_value.warmup.assert_not_called()

    def test_missing_component_cannot_silently_skip(self):
        job = pipeline()
        self.assertFalse(job._pre_solve_captcha())
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "fallback")
        job.logger.warning.assert_called_once()

    def test_pre_solve_actively_runs_and_validates_result(self):
        job = pipeline(**{"captcha.max_attempts": 2})
        job.submit_captcha = Mock(ttl_seconds=170)
        job.submit_captcha.pre_solve.return_value = PreSolvedCaptcha("V", "T")
        job.submit_captcha.ready_for_submission.return_value = True
        self.assertTrue(job._pre_solve_captcha(min_validity=65))
        job.submit_captcha.pre_solve.assert_called_once_with(max_rounds=2)
        job.submit_captcha.ready_for_submission.assert_called_once_with(min_validity=65)
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "ready")

    def test_invalid_result_restores_normal_callback(self):
        job = pipeline()
        job.submit_captcha = Mock()
        job.submit_captcha.ready_for_submission.return_value = False
        job.submit_captcha.discard.return_value = True
        self.assertFalse(job._pre_solve_captcha())
        job.submit_captcha.discard.assert_called_once()
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "fallback")

    def test_disabled_never_opens_captcha(self):
        job = pipeline(**{"captcha.pre_solve": False})
        job.submit_captcha = Mock()
        self.assertFalse(job._pre_solve_captcha())
        job.submit_captcha.pre_solve.assert_not_called()
        self.assertEqual(job.summary["captcha_pre_solve"]["state"], "disabled")

    def test_restore_failure_stops_instead_of_broken_fallback(self):
        job = pipeline()
        job.submit_captcha = Mock()
        job.submit_captcha.discard.return_value = False
        with self.assertRaisesRegex(RuntimeError, "重新启动"):
            job._presolve_fallback("测试失败")

    def test_expired_result_prevents_selection(self):
        job = pipeline()
        job.submit_captcha = Mock()
        job.submit_captcha.ready_for_submission.return_value = False
        job.submit_captcha.discard.return_value = True
        job.booking_manager = Mock()
        self.assertEqual(job._submit_presolved("训练馆3号", ["19:00-20:00"]), "rejected")
        job.booking_manager.select_slots.assert_not_called()

    def test_partial_selection_respects_booking_config(self):
        job = pipeline(**{"booking.allow_partial": False})
        job.submit_captcha = Mock()
        job.booking_manager = Mock()
        job.booking_manager.select_slots.return_value = {"status": "partial"}
        self.assertEqual(job._submit_presolved("训练馆3号", ["19:00-20:00"]), "rejected")
        job.submit_captcha.submit_selected.assert_not_called()

    def test_provider_failure_respects_disabled_local_fallback(self):
        provider = Mock()
        provider.solve.side_effect = RuntimeError("模拟失败")
        solver = ClickWordSolver(provider=provider, fallback_to_local=False)
        solver.read_chars = Mock()
        self.assertIsNone(solver.solve(b"test", ["字"]))
        solver.read_chars.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class PrewarmAndAttemptsTest(unittest.TestCase):
    def test_prewarm_falls_back_to_previous_day(self):
        from datetime import date

        job = pipeline()
        job.query_manager = Mock()
        job.query_manager.focus_date.side_effect = [False, True]
        job._stopped = threading.Event()
        job._prewarm(date(2026, 11, 29))
        calls = [c.args[0] for c in job.query_manager.focus_date.call_args_list]
        self.assertEqual(calls, ["2026-11-29", "2026-11-28"])
        job.logger.info.assert_called_once()
        self.assertIn("2026-11-28", job.logger.info.call_args[0])

    def test_prewarm_skips_previous_day_when_target_visible(self):
        from datetime import date

        job = pipeline()
        job.query_manager = Mock()
        job.query_manager.focus_date.return_value = True
        job._stopped = threading.Event()
        job._prewarm(date(2026, 11, 29))
        job.query_manager.focus_date.assert_called_once_with("2026-11-29", fresh=False)


class MaxAttemptsTest(unittest.TestCase):
    def test_default_is_ten(self):
        self.assertEqual(pipeline()._max_attempts(), 10)

    def test_runtime_override_wins(self):
        self.assertEqual(pipeline(**{"runtime.grab_max_attempts": 14})._max_attempts(), 14)

    def test_falls_back_to_booking_setting(self):
        self.assertEqual(pipeline(**{"booking.max_attempts": 3})._max_attempts(), 3)

    def test_invalid_value_falls_back(self):
        self.assertEqual(pipeline(**{"runtime.grab_max_attempts": "abc"})._max_attempts(), 10)


class ApiBookingTest(unittest.TestCase):
    def job(self, **config):
        job = pipeline(**config)
        job.query_manager = Mock()
        job.query_manager._api_site = 3
        job._provider = Mock()
        job._buddy_ids = {"焦文娟": 998138}
        return job

    def test_api_mode_default_on(self):
        self.assertTrue(self.job()._api_mode())

    def test_api_mode_can_be_disabled(self):
        self.assertFalse(self.job(**{"booking.use_api_submit": False})._api_mode())

    def test_book_via_api_creates_order(self):
        job = self.job()
        client = Mock()
        client.json_get.return_value = {"code": 200, "data": {"id": 130908}}
        job._api_client_or_none = lambda: client
        job._take_api_captcha = lambda c, p: {"verification": "V", "token": "T", "points": [], "at": 0}
        job._now = lambda: datetime.now(timezone.utc)
        cells = {"气膜馆1号": {"09:00-10:00": {"space_id": 3, "time_id": 1, "price": 40}}}
        with patch("src.core.api_booking.submit_order",
                   return_value=(200, '{"code":200,"data":{"id":130908}}')):
            outcome = job._book_via_api(datetime(2026, 11, 30).date(), "气膜馆1号", ["09:00-10:00"], cells)
        self.assertEqual(outcome, BookingOutcome.CREATED)

    def test_book_via_api_returns_none_without_cells(self):
        job = self.job()
        job._api_client_or_none = lambda: Mock()
        self.assertIsNone(job._book_via_api(datetime(2026, 11, 30).date(), "X", ["09:00-10:00"], {}))

    def test_take_captcha_reuses_presolved_within_ttl(self):
        job = self.job(**{"captcha.api_presolve_ttl": 100})
        job._api_captcha = {"verification": "V", "token": "T", "at": 0.0}
        with patch("src.pipeline.ticket_grabbing_pipeline.time.monotonic", return_value=50.0):
            got = job._take_api_captcha(Mock(), Mock())
        self.assertEqual(got["verification"], "V")

    def test_take_captcha_solves_when_expired(self):
        job = self.job(**{"captcha.api_presolve_ttl": 100})
        job._api_captcha = {"verification": "OLD", "token": "T", "at": 0.0}
        with patch("src.pipeline.ticket_grabbing_pipeline.time.monotonic", return_value=500.0), \
                patch("src.core.api_booking.solve_captcha", return_value={"verification": "NEW", "at": 500.0}) as solve:
            got = job._take_api_captcha(Mock(), Mock())
        solve.assert_called_once()
        self.assertEqual(got["verification"], "NEW")
