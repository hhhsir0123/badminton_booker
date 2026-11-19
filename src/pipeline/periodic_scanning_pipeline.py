"""空场轮询：循环查询未来若干天的预约表，发现符合条件的空场就通知/下单。

与旧版的差异：
- 支持多天偏移（0,1,2…），不再是单日；
- 用 stop() 优雅退出，Web 界面可以随时停止；
- 复用 TicketGrabbingPipeline 的扫描与下单逻辑，避免两套 DOM 代码。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .base_pipeline import BasePipeline


class PeriodicScanningPipeline(BasePipeline):
    """周期性扫描流程"""

    def __init__(self, config_path: Optional[str] = None):
        super().__init__(config_path)
        self.scan_interval = int(self.config.get("polling.polling_interval", 60) or 60)
        self.polling_time_slots = self.config.get("polling.polling_time_slots", []) or []
        self.polling_date = self.config.get("polling.polling_date", "") or ""
        self.polling_date_offsets = self.config.get("polling.polling_date_offsets", [0, 1, 2]) or [0]
        self.is_auto_booking = bool(self.config.get("polling.is_auto_booking", False))
        self.partner_name = self.config.get("user.partner_name", "") or ""
        self.venue_type = self.config.get("booking.venue_type", "羽毛球")
        self.is_running = False
        self.summary = {"state": "idle", "mode": "poll"}

    # ------------------------------------------------------------- 配置校验

    def _validate_config(self) -> bool:
        if not getattr(self, "polling_time_slots", None) and not getattr(self, "is_auto_booking", False):
            self.logger.warning("未配置轮询时段，将只读表不下单")
        return True

    def _dates(self) -> List[str]:
        explicit = getattr(self, "polling_date", "")
        if explicit:
            return [explicit]
        offsets = getattr(self, "polling_date_offsets", [0]) or [0]
        today = date.today()
        return [(today + timedelta(days=int(offset))).isoformat() for offset in offsets]

    # ------------------------------------------------------------- 查询

    def _query_available_courts(self) -> Dict[str, List[str]]:
        merged: Dict[str, set] = {}
        for day in self._dates():
            try:
                data = self.query_manager.query_available_courts(day)
            except Exception as error:  # noqa: BLE001
                self.logger.error("查询 %s 失败: %s", day, error)
                continue
            for court, slots in (data or {}).items():
                merged.setdefault(court, set()).update(slots)
        return {court: sorted(slots) for court, slots in merged.items()}

    # ------------------------------------------------------------- 单轮

    def _perform_scan(self):
        available = self._query_available_courts()
        self.summary["last_scan"] = datetime.now().isoformat(timespec="seconds")
        if not available:
            self.logger.info("本轮没有可用场地")
            return

        self.logger.info("找到 %s 个有可订时段的场地", len(available))
        targets = self.polling_time_slots or []
        valid = self._filter_valid_courts(available, targets) if targets else list(available.items())
        if not valid:
            self.logger.info("没有同时满足 %s 的场地", targets)
            return

        booked: List[Tuple[str, List[str]]] = []
        if self.is_auto_booking:
            booked = self._process_bookings(
                valid, targets, bool(self.partner_name), self.partner_name
            )
        self._send_notifications(booked, valid)

    # ------------------------------------------------------------- 通知

    def _send_notifications(self, booked_courts, valid_courts):
        self._send_feishu(booked_courts, valid_courts)
        if not getattr(self, "email_notifier", None):
            return
        user_email = self.config.get("user.email")
        if not user_email:
            return
        try:
            if booked_courts:
                for court, slots in booked_courts:
                    self.email_notifier.send_booking_success(
                        to_email=user_email,
                        booking_info={
                            "venue_type": self.venue_type,
                            "date": self._dates()[0],
                            "time_slot": ", ".join(slots),
                            "court_name": court,
                            "partners": [self.partner_name] if self.partner_name else [],
                        },
                    )
            elif valid_courts:
                self.email_notifier.send_availability_notification(
                    to_email=user_email,
                    available_courts={court: slots for court, slots in valid_courts},
                )
        except Exception as error:  # noqa: BLE001
            self.logger.error("发送邮件失败: %s", error)

    def _send_feishu(self, booked_courts, valid_courts):
        notifier = getattr(self, "feishu_notifier", None)
        if not notifier or not notifier.enabled:
            return
        if booked_courts:
            if not self.config.get("feishu.notify_success", True):
                return
            for court, slots in booked_courts:
                notifier.send_booking_success(
                    self._dates()[0], court, slots,
                    [self.partner_name] if self.partner_name else [],
                )
            return
        if valid_courts and self.config.get("feishu.notify_availability", True):
            notifier.send_availability(
                self._dates()[0],
                {court: slots for court, slots in valid_courts},
            )

    # ------------------------------------------------------------- 循环

    def _wait_for_next_scan(self):
        self._stopped.wait(int(getattr(self, "scan_interval", 60) or 60))

    def run(self) -> bool:
        if not self._validate_config():
            return False
        if not self.start_pipeline():
            self.summary.update(state="failed", message="登录或进入场馆失败")
            self.cleanup()
            return False
        self.is_running = True
        self.summary.update(state="polling", started_at=datetime.now().isoformat(timespec="seconds"))
        self.logger.info("开始轮询：%s，间隔 %ss", ", ".join(self._dates()), self.scan_interval)
        try:
            count = 0
            while not self.stopped:
                count += 1
                self.logger.info("第 %s 轮", count)
                started = time.monotonic()
                self._perform_scan()
                self.summary["rounds"] = count
                self.summary["last_seconds"] = round(time.monotonic() - started, 2)
                self._wait_for_next_scan()
        except Exception as error:  # noqa: BLE001
            self.logger.error("轮询中断: %s", error, exc_info=True)
            self.summary.update(state="failed", message=str(error))
            return False
        finally:
            self.is_running = False
            self.cleanup()
        self.summary.update(state="stopped")
        return True

    def stop(self):
        super().stop()
        self.is_running = False
