"""浏览器管理模块。

为「7 点整抢场」做的调整：
1. 持久化用户目录（profile）：Cookie / 登录态跨进程复用，07:00 不需要重新登录和过验证码；
2. page_load_strategy=eager：DOMContentLoaded 就返回，省掉图片/字体等无关等待；
3. 关闭后台节流：窗口被遮挡或最小化时，页面内的定时器/动画帧仍然正常跑，
   否则注入的轮询会被 Chrome 降到 1 次/秒，抢场直接失效；
4. 清理崩溃残留的 profile 锁，避免下次启动直接失败。
"""
from __future__ import annotations

import os
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

STEALTH = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
"""


class BrowserManager:
    """浏览器管理器"""

    def __init__(self, headless: bool = False, profile_dir: str = ".chrome-profile", project_root: Optional[str] = None):
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.headless = headless
        self.project_root = project_root or os.getcwd()
        self.profile_dir = self._resolve(profile_dir)

    def _resolve(self, path: str) -> str:
        if not path:
            return ""
        return path if os.path.isabs(path) else os.path.join(self.project_root, path)

    def _clear_stale_locks(self):
        """Chrome 崩溃后会在 profile 里留下 SingletonLock，导致下次启动失败。"""
        if not self.profile_dir:
            return
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            target = os.path.join(self.profile_dir, name)
            try:
                if os.path.islink(target) or os.path.exists(target):
                    os.unlink(target)
            except OSError as error:
                logger.debug("清理 %s 失败: %s", target, error)

    def _service(self):
        """优先用项目自带的 chromedriver，避免 Selenium Manager 联网下载。

        跨平台：Windows 是 chromedriver.exe，macOS/Linux 是可执行的 chromedriver。
        会在 drivers/ 下按当前平台挑第一个可用的。
        """
        import glob
        import sys

        from selenium.webdriver.chrome.service import Service

        binary = "chromedriver.exe" if sys.platform.startswith("win") else "chromedriver"
        candidates = [os.environ.get("BB_CHROMEDRIVER", "")]
        # 常见布局：drivers/chromedriver-win64/…、drivers/chromedriver-mac-arm64/…、drivers/chromedriver
        candidates.extend(sorted(glob.glob(os.path.join(self.project_root, "drivers", "**", binary),
                                           recursive=True)))
        candidates.append(os.path.join(self.project_root, "drivers", binary))
        for path in candidates:
            if path and os.path.exists(path) and os.path.isfile(path):
                logger.info("使用本地 chromedriver: %s", path)
                return Service(executable_path=path)
        logger.info("没有找到本地 chromedriver，交给 Selenium Manager 处理")
        return None

    def _options(self) -> Options:
        options = Options()
        options.page_load_strategy = "eager"

        if self.headless:
            options.add_argument("--headless=new")
            logger.warning("无头模式更容易被识别，抢场建议使用有头模式")
        else:
            options.add_argument("--start-maximized")

        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # 保证页面内的 setTimeout/rAF 在后台时也按正常频率运行
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        options.add_argument("--disable-features=CalculateNativeWinOcclusion,Translate")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--lang=zh-CN")
        options.add_argument("--window-size=1280,900")

        if self.profile_dir:
            os.makedirs(self.profile_dir, exist_ok=True)
            options.add_argument(f"--user-data-dir={self.profile_dir}")
            options.add_argument("--profile-directory=Default")
        return options

    def start(self, timeout: int = 20):
        """启动浏览器（重复调用是安全的）。"""
        if self.driver:
            return self.driver
        self._clear_stale_locks()
        logger.info("启动浏览器（profile=%s）", self.profile_dir or "临时")
        service = self._service()
        self.driver = webdriver.Chrome(options=self._options(), service=service) if service else webdriver.Chrome(options=self._options())
        self.driver.set_page_load_timeout(60)
        self.driver.set_script_timeout(45)
        self.wait = WebDriverWait(self.driver, timeout)
        try:
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH}
            )
        except Exception as error:  # noqa: BLE001
            logger.debug("注入反自动化检测脚本失败: %s", error)
        logger.info("浏览器已启动")
        return self.driver

    def open(self, url: str):
        self.start()
        self.driver.get(url)

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception as error:  # noqa: BLE001
                logger.debug("关闭浏览器时出错: %s", error)
            finally:
                self.driver = None
                self.wait = None
                logger.info("浏览器已关闭")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
