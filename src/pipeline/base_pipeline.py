"""流程基类：配置、浏览器生命周期、登录、场馆导航、公共筛选逻辑。

相比旧版：
- 构造时只读配置，不启动浏览器（Web 服务里可以先生成对象，到点再启动）；
- 支持指定配置文件路径；
- 浏览器用持久化 profile，抢场当天不需要重新登录；
- _process_bookings 修正为「每个候选用自己的时段」，并按 BookingOutcome 判断。
"""
from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from ..config import Config
from ..core.booking_result import BookingOutcome
from ..core.browser import BrowserManager
from ..core.court_booking import BookingManager
from ..core.court_query import QueryManager
from ..core.login import LoginManager
from ..utils.captcha_solver import CaptchaSolver
from ..utils.feishu_notifier import FeishuNotifier
from ..utils.logger import setup_logger
from ..utils.ocr import CaptchaRecognizer

logger = setup_logger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BasePipeline(ABC):
    """场馆预订基础流程。"""

    def __init__(self, config_path: Optional[str] = None):
        self.logger = logger
        self.project_root = PROJECT_ROOT
        self.config_path = config_path or os.environ.get("BB_CONFIG") or os.path.join(PROJECT_ROOT, "config.yaml")
        self.config = Config(self.config_path)
        self.browser: Optional[BrowserManager] = None
        self.query_manager: Optional[QueryManager] = None
        self.booking_manager: Optional[BookingManager] = None
        self.login_manager: Optional[LoginManager] = None
        self.captcha_recognizer: Optional[CaptchaRecognizer] = None
        self.captcha_solver: Optional[CaptchaSolver] = None
        self.feishu_notifier: Optional[FeishuNotifier] = self._init_feishu_notifier()
        self._stopped = threading.Event()
        self.summary: Dict[str, object] = {}

    # ------------------------------------------------------------- 生命周期

    def _init_feishu_notifier(self) -> Optional[FeishuNotifier]:
        webhook = self.config.get("feishu.webhook", "")
        if not webhook:
            return None
        notifier = FeishuNotifier(
            webhook,
            secret=self.config.get("feishu.secret", ""),
            mention_all=bool(self.config.get("feishu.mention_all", False)),
        )
        if not notifier.enabled:
            self.logger.warning("飞书 webhook 格式不对，通知已关闭: %s", webhook)
            return None
        self.logger.info("飞书通知已启用")
        return notifier

    def start(self) -> bool:
        """启动浏览器与各管理器（幂等）。"""
        if self.browser and self.browser.driver:
            return True
        try:
            self.browser = BrowserManager(
                headless=bool(self.config.get("login.headless", False)),
                profile_dir=self.config.get("login.profile_dir", ".chrome-profile"),
                project_root=self.project_root,
            )
            self.browser.start()
            self.query_manager = QueryManager(self.browser)
            self.captcha_recognizer = CaptchaRecognizer()
            self.captcha_solver = CaptchaSolver(
                self.browser,
                self.captcha_recognizer,
                max_attempts=int(self.config.get("captcha.max_attempts", 3)),
                min_length=int(self.config.get("captcha.min_length", 4)),
                max_length=int(self.config.get("captcha.max_length", 6)),
                ensemble=bool(self.config.get("captcha.ensemble", True)),
                slider_wait=float(self.config.get("captcha.slider_wait_seconds", 0) or 0),
            )
            self.booking_manager = BookingManager(self.browser, self.query_manager, self.captcha_solver)
            self.login_manager = LoginManager(self.browser, self.captcha_recognizer, self.captcha_solver)
            return True
        except Exception as error:  # noqa: BLE001
            self.logger.error("启动浏览器失败: %s", error, exc_info=True)
            return False

    def start_pipeline(self) -> bool:
        """启动浏览器 → 恢复/执行登录 → 进入场馆页。"""
        if not self.start():
            return False
        assert self.login_manager and self.query_manager
        ok = self.login_manager.ensure_logged_in(
            url=self.config.get("login.url"),
            username=self.config.get("user.username"),
            password=self.config.get("user.password"),
            auto_captcha=bool(self.config.get("login.auto_captcha", True)),
            max_retry=int(self.config.get("login.max_retry", 2)),
        )
        if not ok:
            self.logger.error("登录失败")
            return False

        venue = self.config.get("booking.venue_type", "羽毛球")
        if not self.login_manager.navigate_to_venue(venue):
            self.logger.error("进入场馆失败")
            return False

        self.query_manager.install()
        # 模型加载要 1~2 秒，必须在开售前完成，不能等到 07:00 第一次识别时再加载
        try:
            self.captcha_solver.warmup()
        except Exception as error:  # noqa: BLE001
            self.logger.warning("验证码预热失败: %s", error)
        return True

    def cleanup(self):
        browser = getattr(self, "browser", None)
        if browser:
            browser.close()
            self.browser = None

    def stop(self):
        self._stopped.set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()

    @abstractmethod
    def run(self) -> bool:
        """执行一次完整任务。"""

    # ------------------------------------------------------------- 公共逻辑

    @staticmethod
    def _filter_valid_courts(
        available_courts: Dict[str, List[str]],
        aim_time_slots: Optional[List[str]] = None,
    ) -> List[Tuple[str, List[str]]]:
        """筛选包含全部目标时段的场地。"""
        aim_time_slots = aim_time_slots or []
        valid = []
        for court, times in available_courts.items():
            if all(slot in times for slot in aim_time_slots):
                valid.append((court, times))
        return valid

    def _process_bookings(
        self,
        valid_courts: List[Tuple[str, List[str]]],
        aim_time_slots: Optional[List[str]] = None,
        is_click_parter: bool = False,
        partner_name: str = "",
    ) -> List[Tuple[str, List[str]]]:
        """按顺序尝试预订，成功后停止。"""
        aim_time_slots = aim_time_slots or []
        booked: List[Tuple[str, List[str]]] = []
        partners = [partner_name] if partner_name else []

        for court, times in valid_courts:
            slots = [slot for slot in aim_time_slots if slot in times] or list(times)
            outcome = self.booking_manager.book_court(
                court_name=court,
                time_slots=slots,
                partner_names=partners if is_click_parter else [],
                is_click_parter=bool(is_click_parter and partners),
            )
            if outcome is BookingOutcome.CREATED:
                booked.append((court, slots))
                break
            if outcome is BookingOutcome.UNKNOWN:
                self.logger.warning("提交结果不确定，停止自动重试，请人工核对")
                break
        return booked
