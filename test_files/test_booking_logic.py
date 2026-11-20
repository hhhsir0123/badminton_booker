"""纯逻辑单元测试：不启动浏览器，覆盖时间规划、候选匹配、配置与页面接口映射。"""
import copy
import atexit
import os
import shutil
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config, DEFAULTS, validate_config
from src.core.court_query import QueryManager, parse_slot, slot_key
from src.pipeline.ticket_grabbing_pipeline import (
    booking_plan,
    candidates,
    preferences,
    released,
)

BEIJING = timezone(timedelta(hours=8))
SCRATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")
atexit.register(lambda: shutil.rmtree(SCRATCH, ignore_errors=True))


def scratch_dir() -> str:
    """在仓库内的 .tmp 下建临时目录。

    注意：不用 tempfile.mkdtemp —— DSH 沙箱下 mkdtemp 生成的目录再写文件会被拒绝，
    而普通 makedirs 创建的目录正常。
    """
    os.makedirs(SCRATCH, exist_ok=True)
    path = os.path.join(SCRATCH, "run-" + uuid.uuid4().hex[:10])
    os.makedirs(path, exist_ok=True)
    return path


def make_config(tmpdir, **overrides):
    """写一份临时 config.yaml，方便测试显式字段判断。"""
    import yaml

    data = {"user": {"username": "13800000000"}, "booking": {}, "polling": {}}
    for key, value in overrides.items():
        head, _, tail = key.partition("__")
        data.setdefault(head, {})[tail or head] = value
    path = os.path.join(tmpdir, "config.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True)
    return Config(path)


class BookingPlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = scratch_dir()
        self.config = make_config(self.tmp, booking__booking_days_ahead=2)

    def test_morning_before_release(self):
        opening, play_day = booking_plan(self.config, datetime(2026, 11, 19, 6, 0, tzinfo=BEIJING))
        self.assertEqual(opening, datetime(2026, 11, 19, 7, 0, tzinfo=BEIJING))
        self.assertEqual(play_day, date(2026, 11, 21))

    def test_within_grace_still_today(self):
        opening, play_day = booking_plan(self.config, datetime(2026, 11, 19, 7, 0, 10, tzinfo=BEIJING))
        self.assertEqual(opening.date(), date(2026, 11, 19))
        self.assertEqual(play_day, date(2026, 11, 21))

    def test_after_grace_rolls_to_tomorrow(self):
        opening, play_day = booking_plan(self.config, datetime(2026, 11, 19, 7, 0, 40, tzinfo=BEIJING))
        self.assertEqual(opening, datetime(2026, 11, 20, 7, 0, tzinfo=BEIJING))
        self.assertEqual(play_day, date(2026, 11, 22))

    def test_preview_does_not_roll(self):
        opening, play_day = booking_plan(
            self.config, datetime(2026, 11, 19, 9, 0, tzinfo=BEIJING), preview=True
        )
        self.assertEqual(opening.date(), date(2026, 11, 19))
        self.assertEqual(play_day, date(2026, 11, 21))

    def test_fixed_booking_date(self):
        config = make_config(self.tmp, booking__booking_date="2026-12-06")
        opening, play_day = booking_plan(config, datetime(2026, 11, 19, 9, 0, tzinfo=BEIJING))
        self.assertEqual(play_day, date(2026, 12, 6))
        self.assertEqual(opening, datetime(2026, 12, 4, 7, 0, tzinfo=BEIJING))

    def test_fixed_date_in_the_past(self):
        config = make_config(self.tmp, booking__booking_date="2020-01-01")
        with self.assertRaises(ValueError):
            booking_plan(config, datetime(2026, 11, 19, 9, 0, tzinfo=BEIJING))


class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = scratch_dir()

    def test_new_format(self):
        config = make_config(
            self.tmp,
            booking__preferences=[
                {"court": "任意", "start": "19:00", "end": "21:00"},
                {"court": "训练馆3号", "start": "20:00", "end": "21:00"},
            ],
        )
        choices = preferences(config)
        self.assertEqual(choices[0], (None, ("19:00", "21:00"), ()))
        self.assertEqual(choices[1], ("训练馆3号", ("20:00", "21:00"), ()))

    def test_legacy_format(self):
        config = make_config(self.tmp, booking__preferred_time_slots=["19:00-20:00", "20:00-21:00"])
        choices = preferences(config)
        self.assertEqual(choices, [(None, None, (("19:00", "20:00"), ("20:00", "21:00")))])

    def test_defaults_when_nothing_configured(self):
        config = Config(os.path.join(self.tmp, "missing.yaml"))
        choices = preferences(config)
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0][1], ("19:00", "21:00"))

    def test_invalid_window(self):
        config = make_config(self.tmp, booking__preferences=[
            {"court": "任意", "start": "21:00", "end": "19:00"},
        ])
        with self.assertRaises(ValueError):
            preferences(config)


class CandidatesTest(unittest.TestCase):
    def test_gapless_chain_keeps_page_labels(self):
        availability = {
            "训练馆3号": {
                "19:00-20:00": "19:00~20:00",
                "20:00-21:00": "20:00~21:00",
                "21:00-22:00": "21:00~22:00",
            }
        }
        result = candidates(availability, [(None, ("19:00", "21:00"), ())])
        self.assertEqual(result, [("训练馆3号", ["19:00~20:00", "20:00~21:00"])])

    def test_gap_is_rejected(self):
        availability = {"训练馆3号": {"19:00-20:00": "19:00-20:00", "21:00-22:00": "21:00-22:00"}}
        self.assertEqual(candidates(availability, [(None, ("19:00", "21:00"), ())]), [])

    def test_any_court_priority(self):
        availability = {
            "训练馆4号": {"19:00-20:00": "19:00-20:00"},
            "训练馆3号": {"19:00-20:00": "19:00-20:00"},
        }
        choices = [("训练馆5号", ("19:00", "20:00"), ()), (None, ("19:00", "20:00"), ())]
        result = candidates(availability, choices)
        self.assertEqual([court for court, _ in result], ["训练馆4号", "训练馆3号"])

    def test_named_court_only(self):
        availability = {"训练馆4号": {"19:00-20:00": "19:00-20:00"}}
        self.assertEqual(candidates(availability, [("训练馆3号", ("19:00", "20:00"), ())]), [])

    def test_cutoff_drops_started_slots(self):
        availability = {"训练馆3号": {"18:00-19:00": "18:00-19:00", "19:00-20:00": "19:00-20:00"}}
        # 18:00 已经开始，整段 18:00-20:00 不再成立
        self.assertEqual(candidates(availability, [(None, ("18:00", "20:00"), ())], cutoff="18:30"), [])
        # 只要求 19:00-20:00 时仍然可以订
        self.assertEqual(
            candidates(availability, [(None, ("19:00", "20:00"), ())], cutoff="18:30"),
            [("训练馆3号", ["19:00-20:00"])],
        )

    def test_legacy_grouped(self):
        availability = {"训练馆3号": {"19:00-20:00": "19:00-20:00"}}
        grouped = (("19:00", "20:00"), ("20:00", "21:00"))
        self.assertEqual(candidates(availability, [(None, None, grouped)]), [])

    def test_fallback_any(self):
        availability = {"训练馆3号": {"18:00-19:00": "18:00-19:00", "20:00-21:00": "20:00-21:00"}}
        choices = [("训练馆9号", ("19:00", "21:00"), ())]
        self.assertEqual(candidates(availability, choices), [])
        self.assertEqual(
            candidates(availability, choices, fallback_any=True),
            [("训练馆3号", ["18:00-19:00"])],
        )

    def test_fallback_respects_cutoff(self):
        availability = {"训练馆3号": {"18:00-19:00": "18:00-19:00"}}
        choices = [(None, ("19:00", "21:00"), ())]
        self.assertEqual(
            candidates(availability, choices, cutoff="18:30", fallback_any=True), []
        )


class ReleaseProbeTest(unittest.TestCase):
    def test_body_growth_means_released(self):
        # 响应变大本身不能证明业务成功。
        self.assertFalse(released({"status": 200, "length": 900}, 100))

    def test_valid_target_date_means_ready(self):
        self.assertTrue(released({"status": 200, "ok": True, "target_date_present": True}, None))

    def test_unchanged_body(self):
        self.assertFalse(released({"status": 200, "length": 900}, 900))

    def test_http_error(self):
        self.assertFalse(released({"status": 500, "length": 900}, 100))

    def test_tiny_body(self):
        self.assertFalse(released({"status": 200, "length": 120}, 10))

    def test_no_baseline(self):
        self.assertFalse(released({"status": 200, "length": 900}, None))


class SlotParsingTest(unittest.TestCase):
    def test_common_formats(self):
        self.assertEqual(parse_slot("08:00-09:00"), ("08:00", "09:00"))
        self.assertEqual(parse_slot("08:00~09:00"), ("08:00", "09:00"))
        self.assertEqual(parse_slot("8:00 — 9:00"), ("08:00", "09:00"))
        self.assertEqual(slot_key("19:00 - 20:00"), "19:00-20:00")

    def test_invalid(self):
        self.assertIsNone(parse_slot("场地"))
        self.assertIsNone(parse_slot("09:00-08:00"))


class FreeSlotsTest(unittest.TestCase):
    def test_court_columns(self):
        page = {
            "labels": ["时间段", "训练馆3号", "训练馆4号"],
            "reversed": False,
            "rows": [
                {"label": "19:00-20:00", "cells": [{"classes": ["free"]}, {"classes": ["reserved"]}]},
                {"label": "20:00-21:00", "cells": [{"classes": ["free", "selected"]}, None]},
            ],
        }
        self.assertEqual(
            QueryManager.free_slots(page),
            {"训练馆3号": {"19:00-20:00": "19:00-20:00"}},
        )

    def test_court_rows_and_raw_label(self):
        page = {
            "labels": ["场地", "19:00~20:00"],
            "reversed": True,
            "rows": [{"label": "训练馆3号→", "cells": [{"classes": ["free"]}]}],
        }
        self.assertEqual(
            QueryManager.free_slots(page),
            {"训练馆3号": {"19:00-20:00": "19:00~20:00"}},
        )


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = scratch_dir()
        self.path = os.path.join(self.tmp, "config.yaml")

    def test_defaults_are_valid(self):
        self.assertEqual(validate_config(DEFAULTS), [])

    def test_roundtrip_and_atomic_write(self):
        config = Config(self.path)
        config.set("booking.target_time_in_day", "06:58:00")
        config.save()
        self.assertTrue(os.path.exists(self.path))
        again = Config(self.path)
        self.assertEqual(again.get("booking.target_time_in_day"), "06:58:00")
        self.assertEqual([name for name in os.listdir(self.tmp) if name.startswith(".config-")], [])

    def test_validation_messages(self):
        config = Config(self.path)
        config.set("booking.target_time_in_day", "7:00")
        config.set("booking.preferences", [])
        errors = validate_config(config.config)
        self.assertTrue(any("开售时间" in error for error in errors))
        self.assertTrue(any("偏好" in error for error in errors))

    def test_explicit_tracking(self):
        config = make_config(self.tmp, booking__preferred_time_slots=["19:00-20:00"])
        self.assertTrue(config.has_explicit("booking.preferred_time_slots"))
        self.assertFalse(config.has_explicit("booking.preferences"))


class PipelineOverrideTest(unittest.TestCase):
    """--date 之类的临时覆盖，不应该改动 config.yaml。"""

    def test_override_wins_without_touching_file(self):
        from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline

        tmp = scratch_dir()
        path = os.path.join(tmp, "config.yaml")
        Config(path).save()
        before = open(path, encoding="utf-8").read()

        pipeline = TicketGrabbingPipeline(path, overrides={"booking.booking_date": "2026-12-06"})
        self.assertEqual(pipeline.config.get("booking.booking_date"), "2026-12-06")
        self.assertEqual(open(path, encoding="utf-8").read(), before)


class ApiSlotsTest(unittest.TestCase):
    """接口口径：只有 status=1 且没人占用（含未支付）才算可订。"""

    def _payload(self, status, already=0, wait=0):
        return {
            "reservationDateSpaceInfo": {
                "2026-11-20": [
                    {
                        "16038": {
                            "spaceName": "气膜馆5号",
                            "startDate": "2026-11-20 11:00",
                            "endDate": "2026-11-20 12:00",
                            "reservationStatus": status,
                            "alreadyNum": already,
                            "waitNum": wait,
                        }
                    }
                ]
            }
        }

    def test_free(self):
        from src.core.api_client import ApiClient

        self.assertEqual(
            ApiClient.parse_slots(self._payload(1), "2026-11-20"),
            {"气膜馆5号": {"11:00-12:00": "11:00-12:00"}},
        )

    def test_unpaid_hold_excluded(self):
        from src.core.api_client import ApiClient

        self.assertEqual(ApiClient.parse_slots(self._payload(3), "2026-11-20"), {})

    def test_booked_excluded(self):
        from src.core.api_client import ApiClient

        self.assertEqual(ApiClient.parse_slots(self._payload(4), "2026-11-20"), {})

    def test_partially_taken_excluded(self):
        from src.core.api_client import ApiClient

        self.assertEqual(ApiClient.parse_slots(self._payload(1, already=1), "2026-11-20"), {})


class ExampleConfigTest(unittest.TestCase):
    """config.example.yaml 必须能被程序读进来并通过校验，否则新用户第一步就翻车。"""

    def test_example_is_valid(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.yaml"
        )
        config = Config(path)
        self.assertEqual(validate_config(config.config), [])
        choices = preferences(config)
        self.assertEqual(choices[0][1], ("19:00", "21:00"))
        self.assertEqual(choices[1][0], "训练馆3号")

    def test_example_days_ahead(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.yaml"
        )
        config = Config(path)
        self.assertEqual(config.get("booking.booking_days_ahead"), 2)
        self.assertEqual(config.get("runtime.release_probe_interval_ms"), 200)


if __name__ == "__main__":
    unittest.main()

class WholeHourTest(unittest.TestCase):
    def test_reject_half_hour(self):
        data = copy.deepcopy(DEFAULTS)
        data["booking"]["preferences"] = [{"court": "任意", "start": "19:30", "end": "21:00"}]
        self.assertTrue(any("整点" in item for item in validate_config(data)))

    def test_accept_whole_hour(self):
        data = copy.deepcopy(DEFAULTS)
        data["booking"]["preferences"] = [{"court": "任意", "start": "19:00", "end": "21:00"}]
        self.assertFalse(any("整点" in item for item in validate_config(data)))

