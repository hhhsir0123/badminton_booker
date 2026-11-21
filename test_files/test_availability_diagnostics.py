"""离线回归：日期窗口恢复、业务校验、失败分类与准确计数。"""
import json
import os
import sys
import threading
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.api_client import ApiClient
from src.core.court_query import QueryManager
from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline, released

DAY = "2026-11-22"


def response(rows):
    return json.dumps({"code": 200, "data": {"reservationDateSpaceInfo": {DAY: rows}}})


class ProbeValidationTest(unittest.TestCase):
    def test_http_success_business_failure_is_not_release(self):
        result = ApiClient.inspect_day_response(200, json.dumps({"code": 401, "message": "private"}), DAY)
        self.assertEqual(result["error"], "business-error")
        self.assertNotIn("private", str(result))
        self.assertFalse(released(dict(result, status=200, length=90000), 71))

    def test_missing_date_is_not_empty_inventory(self):
        result = ApiClient.inspect_day_response(200, json.dumps({"code": 200, "data": {"reservationDateSpaceInfo": {}}}), DAY)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "target-date-missing")

    def test_malformed_json_and_shape(self):
        for body in ["<html>error</html>", "[]", '{"code":200,"data":null}']:
            self.assertFalse(ApiClient.inspect_day_response(200, body, DAY)["ok"])

    def test_bad_cells_are_not_inventory(self):
        for cell in [None, "bad", {}, {"reservationStatus": 1}, {"reservationStatus": 99}]:
            result = ApiClient.inspect_day_response(200, response([{"x": cell}]), DAY)
            self.assertEqual(result["error"], "invalid-slot-data")
            self.assertTrue(result["target_date_present"])

    def test_live_row_metadata_is_not_a_slot(self):
        result = ApiClient.inspect_day_response(200, response([{
            "venueSpaceGroupId": None, "spaceName": "训练馆3号", "enSpaceName": None,
            "id": 3, "venueSiteId": 3,
            "time-id": {"reservationStatus": 4, "spaceName": "训练馆3号"}}]), DAY)
        self.assertTrue(result["ok"])
        self.assertTrue(result["target_date_present"])
        self.assertEqual(result["free_slots"], 0)

    def test_occupied_and_valid_empty_date_are_successful_reads(self):
        for rows in [[], [{"x": {"reservationStatus": 4}}]]:
            result = ApiClient.inspect_day_response(200, response(rows), DAY)
            self.assertTrue(result["ok"])
            self.assertEqual(result["free_slots"], 0)

    def test_free_cells_counted(self):
        result = ApiClient.inspect_day_response(200, response([{"x": {
            "reservationStatus": 1, "spaceName": "训练馆3号", "alreadyNum": 0,
            "startDate": DAY + " 19:00", "endDate": DAY + " 20:00"}}]), DAY)
        self.assertTrue(result["ok"])
        self.assertEqual(result["free_slots"], 1)

    def test_http_failure(self):
        self.assertEqual(ApiClient.inspect_day_response(503, "private", DAY)["error"], "http-error")


class DateRecoveryTest(unittest.TestCase):
    def manager(self, recover):
        q = QueryManager(None)
        old = [{"text": "11月20日", "cls": "active"}, {"text": "11月21日", "cls": ""}]
        q.tabs = Mock(return_value=old)
        q.today = lambda: date(2026, 11, 20)
        q._wait_false = lambda predicate, timeout: True
        q._ensure_helpers = lambda: None
        q._async = lambda *args: True
        q._loaded_response = Mock(return_value=None)
        q._wait_loaded_response = lambda wanted: q._loaded_response(wanted)
        q._click_tab = Mock(return_value={"ok": True, "ms": 12})
        def reload():
            if recover:
                q.tabs.return_value = old + [{"text": "11月22日", "cls": "active"}]
        q.reload = Mock(side_effect=reload)
        return q

    def test_missing_tab_recovers_before_reload_mode_can_early_return(self):
        for mode in ["auto", "reload"]:
            q = self.manager(True)
            self.assertTrue(q.focus_date(DAY, fresh=True, refresh=mode))
            q.reload.assert_called_once()
            q._click_tab.assert_called_once()

    def test_still_missing_is_query_failure_with_visible_dates(self):
        q = self.manager(False)
        self.assertFalse(q.focus_date(DAY, fresh=True))
        self.assertEqual(q.last_query_status["error"], "target-date-missing")
        self.assertEqual(len(q.last_query_status["visible_dates"]), 2)

    def test_no_response_is_failure_even_if_selected(self):
        q = self.manager(True)
        q._click_tab.return_value = {"ok": False, "error": "no-response"}
        self.assertFalse(q.focus_date(DAY, fresh=True, refresh="reload"))
        self.assertEqual(q.last_query_status["error"], "no-response")

    def test_reloaded_active_date_uses_validated_initial_response(self):
        q = self.manager(True)
        q._loaded_response.return_value = {"ok": True, "ms": 4}
        self.assertTrue(q.focus_date(DAY, fresh=True))
        q._click_tab.assert_not_called()

    def test_existing_active_date_forced_reload_uses_fresh_response(self):
        q = self.manager(True)
        q.tabs.return_value += [{"text": "11月22日", "cls": "active"}]
        q._loaded_response.return_value = {"ok": True, "ms": 4}
        self.assertTrue(q.focus_date(DAY, fresh=True, refresh="reload"))
        self.assertTrue(q.last_query_status["refreshed"])
        q.reload.assert_called_once()
        q._click_tab.assert_not_called()

    def test_malformed_snapshot_is_failure_but_valid_empty_table_is_readable(self):
        q = QueryManager(None)
        for raw in [{"labels": [], "rows": []}, {"labels": ["时间段", "训练馆3号"],
                    "rows": [{"label": "暂无数据", "cells": []}]}]:
            q._async = lambda *args: raw
            self.assertIsNone(q.snapshot())
        raw = {"labels": ["时间段", "训练馆3号"], "rows": []}
        q._async = lambda *args: raw
        self.assertEqual(q.snapshot(), raw)
        raw["labels"][0] = ""
        self.assertEqual(q.snapshot(), raw)


class AvailabilityClassificationTest(unittest.TestCase):
    def pipeline(self, pages=None, focus=True, page_error=None):
        p = TicketGrabbingPipeline.__new__(TicketGrabbingPipeline)
        p.summary = {}
        p.logger = Mock()
        p._stopped = threading.Event()
        p.config = SimpleNamespace(get=lambda key, default=None: default)
        p._clear_captcha = lambda: True
        q = Mock()
        q.focus_date.return_value = focus
        q.last_query_status = {"error": "target-date-missing"} if not focus else {"ok": True}
        q.last_page_error = page_error
        q.iter_pages.side_effect = lambda: iter(pages or [])
        q.free_slots.side_effect = lambda page: page
        p.query_manager = q
        return p

    def read(self, p, choices=None):
        return p._candidates_for(DAY, datetime(2026, 11, 20, 7), choices or [(None, ("19:00", "20:00"), ())])

    def test_missing_date_is_not_no_inventory(self):
        p = self.pipeline(focus=False)
        self.assertEqual(self.read(p), [])
        self.assertEqual(p.summary["last_availability"]["status"], "query_failed")
        self.assertEqual(p.summary.get("availability_reads", 0), 0)
        state, message = p._availability_failure()
        self.assertEqual(state, "failed")
        self.assertIn("不能判断库存", message)

    def test_missing_table_is_failure(self):
        p = self.pipeline([])
        self.read(p)
        self.assertEqual(p.summary["last_availability"]["error"], "table-missing")

    def test_successful_empty_table_is_no_inventory(self):
        p = self.pipeline([{}])
        self.read(p)
        self.assertEqual(p.summary["last_availability"]["status"], "no_inventory")
        self.assertEqual(p.summary["availability_reads"], 1)
        self.assertIn("成功读取未发现", p._availability_failure()[1])

    def test_inventory_without_preferred_match(self):
        p = self.pipeline([{"训练馆3号": {"18:00-19:00": "18:00-19:00"}}])
        self.read(p)
        self.assertEqual(p.summary["last_availability"]["status"], "no_match")
        self.assertIn("不符合预约偏好", p._availability_failure()[1])

    def test_partial_pagination_is_failure(self):
        p = self.pipeline([{"训练馆3号": {"19:00-20:00": "19:00-20:00"}}], page_error="pagination-failed")
        self.assertEqual(self.read(p), [])
        self.assertEqual(p.summary["last_availability"]["status"], "query_failed")
        self.assertEqual(p.summary.get("availability_reads", 0), 0)

    def test_read_exception_is_failure(self):
        p = self.pipeline()
        p.query_manager.focus_date.side_effect = RuntimeError("secret")
        self.read(p)
        self.assertEqual(p.summary["last_availability"]["error"], "RuntimeError")
        self.assertNotIn("secret", str(p.summary))

    def test_probe_and_successful_read_counts_separate_on_success(self):
        p = self.pipeline([{"训练馆3号": {"19:00-20:00": "19:00-20:00"}}])
        p._now = lambda: datetime(2026, 11, 20, 7)
        p._wait_until = lambda _: True
        p.query_manager.probe.return_value = {"status": 200, "ok": True, "target_date_present": True, "ms": 1}
        self.assertTrue(p._grab(DAY, p._now(), [(None, ("19:00", "20:00"), ())]))
        self.assertEqual(p.summary["probe_queries"], 1)
        self.assertEqual(p.summary["availability_attempts"], 1)
        self.assertEqual(p.summary["availability_reads"], 1)

    def test_latest_failed_read_does_not_reuse_previous_empty_result(self):
        p = self.pipeline([{}])
        self.read(p)
        p.query_manager.focus_date.return_value = False
        self.read(p)
        self.assertEqual(p.summary["availability_reads"], 1)
        self.assertEqual(p._availability_failure()[0], "failed")


if __name__ == "__main__":
    unittest.main()
