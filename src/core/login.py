"""登录与场馆导航。

关键点：
- 优先复用持久化 profile 里的登录态：07:00 的脚本不应该在放场前几十秒才登录，
  更不应该在那一刻识别验证码；
- 把原来写死的 time.sleep(3/2/5) 换成条件等待，登录耗时从十几秒降到 2~4 秒；
- 进入场馆页面的判定改成「日期选择条出现」，比原来的 URL/标题猜测可靠。
"""
from __future__ import annotations

import time
from typing import Optional

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

CAPTCHA_INPUTS = [
    "input[placeholder*='验证码']",
    "input[name*='captcha']",
    "input[name*='code']",
    "input[name='verifyCode']",
]


class LoginManager:
    """登录管理器"""

    def __init__(self, browser, ocr, solver=None):
        self.browser = browser
        self.ocr = ocr
        self.solver = solver

    @property
    def driver(self):
        return self.browser.driver

    # ------------------------------------------------------------------ 入口

    def ensure_logged_in(self, url: str, username: str, password: str,
                         auto_captcha: bool = True, max_retry: int = 2) -> bool:
        """打开站点；有会话就直接复用，没有才登录。"""
        logger.info("打开站点: %s", url)
        self.driver.get(url)

        form = self._wait_form(10)
        if form is None:
            logger.info("没有出现登录表单，复用已有登录态")
            return True

        if not username or not password:
            logger.error("需要登录但配置里没有账号密码")
            return False

        for attempt in range(max_retry):
            if self._login_once(username, password, auto_captcha, form):
                logger.info("登录成功")
                return True
            form = "password"
            logger.warning("登录失败，重试 %s/%s", attempt + 1, max_retry)
            time.sleep(1)
        return False

    # 兼容旧调用
    def login(self, url: str, username: str, password: str,
              auto_captcha: bool = True, max_retry: int = 2) -> bool:
        return self.ensure_logged_in(url, username, password, auto_captcha, max_retry)

    # ------------------------------------------------------------- 登录内部

    def _wait_form(self, timeout: float) -> Optional[str]:
        """等待登录表单出现。返回 'external' / 'password' / None。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self._visible(By.CSS_SELECTOR, "input[type='password']"):
                    return "password"
                if self.driver.find_elements(By.XPATH, "//*[contains(text(), '校外用户')]"):
                    return "external"
            except Exception:  # noqa: BLE001 - 页面跳转期间会短暂失败
                pass
            time.sleep(0.1)
        return None

    def _visible(self, by, selector) -> bool:
        return any(element.is_displayed() for element in self.driver.find_elements(by, selector))

    def _login_once(self, username: str, password: str, auto_captcha: bool, form: str) -> bool:
        if form == "external" and not self._click_external_user():
            return False
        if not self._fill_credentials(username, password):
            return False
        if not self._handle_captcha(auto_captcha):
            return False
        # 有些实现里验证码通过后会自动提交登录，页面已经跳走
        if self._wait_logged_in(2) or not self._visible(By.CSS_SELECTOR, "input[type='password']"):
            return True
        if not self._click_login_button():
            return self._wait_logged_in(3)
        return self._wait_logged_in(12)

    def _click_external_user(self) -> bool:
        try:
            element = WebDriverWait(self.driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), '校外用户')]"))
            )
            self.driver.execute_script("arguments[0].click();", element)
            logger.info("已切换到校外用户登录")
            return self._wait_for(
                lambda: self._visible(By.CSS_SELECTOR, "input[type='password']"), 8
            )
        except TimeoutException:
            logger.error("未找到「校外用户」入口")
            return False

    def _fill_credentials(self, username: str, password: str) -> bool:
        try:
            field = WebDriverWait(self.driver, 6).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "input[type='text'], input[name='username']")
                )
            )
            field.clear()
            field.send_keys(username)
            secret = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            secret.clear()
            secret.send_keys(password)
            return True
        except Exception as error:  # noqa: BLE001
            logger.error("填写账号密码失败: %s", error)
            return False

    def _handle_captcha(self, auto_captcha: bool) -> bool:
        field = None
        for selector in CAPTCHA_INPUTS:
            elements = [e for e in self.driver.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed()]
            if elements:
                field = elements[0]
                break
        if not field:
            return True

        logger.info("检测到图形验证码")
        if not auto_captcha:
            code = input("请输入验证码: ").strip()
            field.send_keys(code)
            return bool(code)

        if self.solver is not None:
            return self.solver.solve()

        image = self._find_captcha_image()
        if not image:
            logger.error("未找到验证码图片")
            return False
        code = self.ocr.recognize(image.screenshot_as_png)
        if not code:
            logger.error("验证码识别失败")
            return False
        field.clear()
        field.send_keys(code)
        logger.info("验证码已填入: %s", code)
        return True

    def _find_captcha_image(self):
        selectors = [
            "span[data-v-57a28888] > img",
            "div.ivu-form-item-content img",
            "img[src^='data:image']",
            "img[src*='captcha']",
        ]
        for selector in selectors:
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and element.size.get("width"):
                    return element
        return None

    def _click_login_button(self) -> bool:
        try:
            candidates = self.driver.find_elements(
                By.XPATH, "//button[contains(., '登录') or contains(., '登 录')]"
            )
            if not candidates:
                candidates = self.driver.find_elements(By.CSS_SELECTOR, "button.ivu-btn-primary")
            for button in candidates:
                if button.is_displayed() and not button.get_attribute("disabled"):
                    self.driver.execute_script("arguments[0].click();", button)
                    return True
            field = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            field.send_keys(Keys.ENTER)
            return True
        except (NoSuchElementException, Exception) as error:  # noqa: BLE001
            logger.error("点击登录按钮失败: %s", error)
            return False

    def _wait_logged_in(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if "login" not in self.driver.current_url:
                    return True
                message = self._error_text()
                if message:
                    if "验证码" in message:
                        logger.warning("验证码错误: %s", message)
                    else:
                        logger.error("登录报错: %s", message)
                    return False
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.15)
        return "login" not in self.driver.current_url

    def _error_text(self) -> Optional[str]:
        for selector in (".ivu-message", ".error", ".alert", ".el-message"):
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed():
                    text = (element.text or "").strip()
                    if text:
                        return text
        return None

    def _wait_for(self, predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.1)
        return False

    # ------------------------------------------------------------- 场馆导航

    def on_venue_page(self) -> bool:
        try:
            return bool(self.driver.execute_script(
                "return !!document.querySelector('.date_box > div, .reserveTable .spaceTable table');"
            ))
        except Exception:  # noqa: BLE001
            return False

    def navigate_to_venue(self, venue_name: str = "羽毛球") -> bool:
        """进入场馆页；以「日期选择条出现」为成功判据。"""
        if self.on_venue_page():
            logger.info("已经在场馆页面")
            return True

        logger.info("进入场馆: %s", venue_name)
        # 登录后首页的场馆列表是异步渲染的，第一次经常还看不到；等一会儿并刷新重试
        element = None
        for attempt in range(1, 4):
            element = self._wait_venue_entry(venue_name, 6)
            if element:
                break
            logger.warning("第 %s 次没找到场馆入口（%s），刷新首页重试", attempt, venue_name)
            try:
                self.driver.get(self._venue_home_url())
                time.sleep(1.0)
            except Exception as error:  # noqa: BLE001
                logger.debug("刷新首页失败: %s", error)
        if not element:
            logger.error("未找到场馆入口: %s", venue_name)
            self._screenshot("venue_not_found.png")
            return False

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        try:
            element.click()
        except Exception:  # noqa: BLE001
            self.driver.execute_script("arguments[0].click();", element)

        if not self._wait_for(self.on_venue_page, 20):
            logger.error("进入 %s 后没有看到预约表", venue_name)
            self._screenshot("venue_not_loaded.png")
            return False

        # 有些校区会弹公告，关掉它，避免挡住场格
        self._close_modal()
        logger.info("已进入 %s 场馆页面", venue_name)
        return True

    def _venue_home_url(self) -> str:
        try:
            from urllib.parse import urlparse

            parts = urlparse(self.driver.current_url)
            return f"{parts.scheme}://{parts.netloc}/venue/home"
        except Exception:  # noqa: BLE001
            return "https://ggtypt.cumtb.edu.cn/venue/home"

    def _wait_venue_entry(self, venue_name: str, timeout: float = 6.0):
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            try:
                element = self._find_venue(venue_name)
            except Exception:  # noqa: BLE001
                element = None
            if element:
                return element
            time.sleep(0.1)
        return None

    def _find_venue(self, venue_name: str):
        xpaths = [
            f"//dd[contains(., '{venue_name}')]",
            f"//dt[contains(., '{venue_name}')]",
            f"//a[contains(., '{venue_name}')]",
            f"//*[contains(text(), '{venue_name}')]/ancestor-or-self::*[self::dd or self::dt or self::a or self::li][1]",
            f"//*[contains(text(), '{venue_name}')]",
        ]
        for xpath in xpaths:
            elements = self.driver.find_elements(By.XPATH, xpath)
            for element in elements:
                try:
                    if element.is_displayed():
                        return element
                except Exception:  # noqa: BLE001
                    continue
        return None

    def _close_modal(self) -> bool:
        try:
            buttons = self.driver.find_elements(
                By.XPATH, "//button[.//span[normalize-space(text())='关闭'] or normalize-space(text())='关闭']"
            )
            for button in buttons:
                if button.is_displayed():
                    self.driver.execute_script("arguments[0].click();", button)
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _screenshot(self, name: str):
        try:
            self.driver.save_screenshot(name)
        except Exception:  # noqa: BLE001
            pass
