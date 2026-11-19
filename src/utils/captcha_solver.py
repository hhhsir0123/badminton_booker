"""验证码求解：预热、快速取图、多预处理投票、自动刷新重试。

为什么单独做一层：
- 07:00 才第一次加载 ddddocr 模型要 1~2 秒，这是藏在链路里的时间炸弹；
- `element.screenshot_as_png` 是一次 WebDriver 往返 + PNG 编码，100~300ms；
  同源图片用页面内 canvas.toDataURL 取，一次往返、几十毫秒；
- 单次识别错了就要刷新重试，所以正确率直接等于耗时。这里用多种预处理各识别一次再投票。

只处理字符型验证码（数字/字母）：ddddocr 分类模型 + 多预处理投票。
点选验证码由 src/core/submit_captcha.py 负责。
"""
from __future__ import annotations

import base64
import io
import re
import time
from typing import Dict, List, Optional, Tuple

from .logger import setup_logger

logger = setup_logger(__name__)

INPUT_SELECTORS = [
    "input[placeholder*='验证码']",
    "input[name*='captcha']",
    "input[name*='verifyCode']",
    "input[name*='code']",
]

IMAGE_SELECTORS = [
    "span[data-v-57a28888] > img",
    "div.ivu-form-item-content img",
    "img[src*='captcha']",
    "img[src^='data:image']",
]

CLICK_HINT = "//*[contains(text(), '请依次点击')]"
CONFIRM_TEXTS = ("确定", "确认", "提交", "验证", "登录")

JS_PRESENT = """
const sels = arguments[0];
for (const sel of sels) {
  const nodes = [...document.querySelectorAll(sel)];
  if (nodes.some((el) => el.getClientRects().length)) { return true; }
}
return false;
"""

JS_CAPTURE = """
const img = arguments[0];
try {
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  if (!canvas.width || !canvas.height) { return null; }
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/png');
} catch (error) {
  return null;
}
"""

JS_FILL = """
const [el, value] = arguments;
if (!el) { return false; }
const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
setter.call(el, value);
el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
return el.value === value;
"""

JS_CONFIRM = """
const [input, texts] = arguments;
let scope = input;
for (let i = 0; i < 4 && scope && scope.parentElement; i += 1) { scope = scope.parentElement; }
const candidates = [...scope.querySelectorAll('button, .ivu-btn, [role="button"]')]
  .filter((el) => el.getClientRects().length);
for (const el of candidates) {
  const label = (el.innerText || '').replace(/\\s+/g, '');
  if (texts.some((text) => label === text || label.includes(text))) {
    el.click();
    return true;
  }
}
return false;
"""


class CaptchaSolver:
    """在页面里完成「取图 → 识别 → 填写 → 确认 → 验证消失」。"""

    def __init__(self, browser, recognizer, max_attempts: int = 3,
                 min_length: int = 4, max_length: int = 6, ensemble: bool = True):
        self.browser = browser
        self.recognizer = recognizer
        self.max_attempts = max(1, int(max_attempts))
        self.min_length = max(1, int(min_length))
        self.max_length = max(self.min_length, int(max_length))
        self.ensemble = bool(ensemble)
        self.last_variants: List[Tuple[str, str]] = []

    @property
    def driver(self):
        return self.browser.driver

    # ------------------------------------------------------------- 检测

    def present(self) -> bool:
        try:
            return bool(self.driver.execute_script(JS_PRESENT, INPUT_SELECTORS))
        except Exception:  # noqa: BLE001
            return False

    def _input_element(self):
        for selector in INPUT_SELECTORS:
            for element in self.driver.find_elements("css selector", selector):
                try:
                    if element.is_displayed():
                        return element
                except Exception:  # noqa: BLE001
                    continue
        return None

    # 验证码图片通常是 80x26 左右；太小的多半是页面图标
    MIN_WIDTH = 40
    MIN_HEIGHT = 14

    def _image_element(self):
        # 优先：验证码输入框所在表单项里的图片
        field = self._input_element()
        if field is not None:
            try:
                from selenium.webdriver.common.by import By

                for element in field.find_elements(
                    By.XPATH, "./ancestor::div[contains(@class,'ivu-form-item')][1]//img"
                ):
                    if self._looks_like_captcha(element):
                        return element
            except Exception:  # noqa: BLE001
                pass

        for selector in IMAGE_SELECTORS:
            for element in self.driver.find_elements("css selector", selector):
                if self._looks_like_captcha(element):
                    return element
        return None

    def _looks_like_captcha(self, element) -> bool:
        try:
            if not element.is_displayed():
                return False
            size = element.size or {}
            width = int(size.get("width") or 0)
            height = int(size.get("height") or 0)
            return width >= self.MIN_WIDTH and height >= self.MIN_HEIGHT
        except Exception:  # noqa: BLE001
            return False

    def _click_hint(self) -> Optional[str]:
        try:
            for element in self.driver.find_elements("xpath", CLICK_HINT):
                if element.is_displayed():
                    return (element.text or "").strip()
        except Exception:  # noqa: BLE001
            pass
        return None

    # ------------------------------------------------------------- 取图

    def capture(self, element) -> Optional[bytes]:
        """优先用页面内 canvas（快），失败再退回截图（慢但兼容跨域图片）。"""
        try:
            data_url = self.driver.execute_script(JS_CAPTURE, element)
        except Exception:  # noqa: BLE001
            data_url = None
        if isinstance(data_url, str) and "," in data_url:
            try:
                return base64.b64decode(data_url.split(",", 1)[1])
            except Exception:  # noqa: BLE001
                pass
        try:
            return element.screenshot_as_png
        except Exception as error:  # noqa: BLE001
            logger.debug("验证码取图失败: %s", error)
            return None

    # ------------------------------------------------------------- 预处理

    @staticmethod
    def _otsu(gray) -> int:
        histogram = gray.histogram()
        total = sum(histogram)
        if not total:
            return 127
        total_sum = sum(index * count for index, count in enumerate(histogram))
        weight_back = 0
        sum_back = 0
        best_variance = 0.0
        threshold = 127
        for level in range(256):
            weight_back += histogram[level]
            if not weight_back:
                continue
            weight_forward = total - weight_back
            if not weight_forward:
                break
            sum_back += level * histogram[level]
            mean_back = sum_back / weight_back
            mean_forward = (total_sum - sum_back) / weight_forward
            variance = weight_back * weight_forward * (mean_back - mean_forward) ** 2
            if variance > best_variance:
                best_variance = variance
                threshold = level
        return threshold

    @classmethod
    def variants(cls, png: bytes) -> List[Tuple[str, bytes]]:
        """返回多种预处理结果；没有 Pillow 时退化成原图一种。"""
        try:
            from PIL import Image, ImageFilter, ImageOps
        except ImportError:
            return [("raw", png)]

        try:
            image = Image.open(io.BytesIO(png)).convert("RGB")
        except Exception:  # noqa: BLE001
            return [("raw", png)]

        def encode(img) -> bytes:
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()

        results: List[Tuple[str, bytes]] = [("raw", png)]
        gray = ImageOps.grayscale(image)
        threshold = cls._otsu(gray)
        binarized = gray.point(lambda value: 255 if value > threshold else 0)

        bigger = (gray.width * 2, gray.height * 2)
        results.append(("gray2x", encode(gray.resize(bigger, Image.LANCZOS))))
        results.append(("otsu2x", encode(binarized.resize(bigger, Image.LANCZOS))))
        results.append(("invert", encode(ImageOps.invert(gray))))
        results.append(("median", encode(gray.filter(ImageFilter.MedianFilter(3)))))
        return results

    # ------------------------------------------------------------- 识别

    def _normalize(self, text: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", str(text or ""))

    def recognize(self, png: bytes) -> Optional[str]:
        """多预处理投票；返回长度合法的结果，票数不够就返回 None。"""
        variants = self.variants(png) if self.ensemble else [("raw", png)]
        guesses: List[str] = []
        self.last_variants = []
        for name, payload in variants:
            try:
                text = self.recognizer.ocr.classification(payload)
            except Exception as error:  # noqa: BLE001
                logger.debug("变体 %s 识别失败: %s", name, error)
                continue
            cleaned = self._normalize(text)
            self.last_variants.append((name, cleaned or "-"))
            if self.min_length <= len(cleaned) <= self.max_length:
                guesses.append(cleaned)

        if not guesses:
            return None
        # 大小写不敏感地投票，但返回第一次出现的原始写法（有些平台区分大小写）
        counts: Dict[str, int] = {}
        canonical: Dict[str, str] = {}
        for guess in guesses:
            key = guess.upper()
            counts[key] = counts.get(key, 0) + 1
            canonical.setdefault(key, guess)
        best_key, votes = max(counts.items(), key=lambda item: item[1])
        text = canonical[best_key]
        if votes >= 2:
            logger.info("验证码投票: %s（%s/%s 一致）", text, votes, len(guesses))
            return text
        logger.info("验证码只有 1 票，按最好结果使用: %s（%s）", text, self.last_variants)
        return text

    # ------------------------------------------------------------- 填写

    def fill(self, element, text: str) -> bool:
        try:
            element.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.driver.execute_script(JS_FILL, element, text):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            element.send_keys(text)
            return True
        except Exception as error:  # noqa: BLE001
            logger.debug("填写验证码失败: %s", error)
            return False

    def confirm(self, element) -> bool:
        try:
            if self.driver.execute_script(JS_CONFIRM, element, list(CONFIRM_TEXTS)):
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            element.send_keys("\n")
            return True
        except Exception:  # noqa: BLE001
            return False

    def _wait_gone(self, timeout: float = 1.5) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.present():
                return True
            time.sleep(0.05)
        return False

    def refresh(self) -> bool:
        """刷新验证码：优先点刷新图标，没有就等图片自己变。"""
        try:
            for element in self.driver.find_elements(
                "xpath", "//i[contains(@class, 'refresh')] | //*[contains(text(), '换一张')]"
            ):
                if element.is_displayed():
                    element.click()
                    time.sleep(0.15)
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    # ------------------------------------------------------------- 主流程

    def solve(self, max_attempts: Optional[int] = None) -> bool:
        """有验证码就解决它；没有直接返回 True。"""
        if not self.present():
            return True

        # 点选验证码由 src/core/submit_captcha.py 负责，这里只处理字符型
        if self._click_hint():
            logger.info("检测到点选验证码，交给 SubmitCaptchaFlow 处理")
            return False

        attempts = max_attempts or self.max_attempts
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            element = self._input_element()
            image = self._image_element()
            if not element or not image:
                logger.warning("验证码输入框或图片找不到")
                return False
            png = self.capture(image)
            if not png:
                logger.warning("验证码取图失败")
                return False
            code = self.recognize(png)
            elapsed = int((time.monotonic() - started) * 1000)
            if not code:
                logger.warning("第 %s 次识别失败（%sms），刷新验证码", attempt, elapsed)
                self.refresh()
                continue
            if not self.fill(element, code):
                return False
            self.confirm(element)
            if self._wait_gone(1.5):
                logger.info("验证码已通过（第 %s 次，识别+填写 %sms）", attempt, elapsed)
                return True
            logger.warning("第 %s 次验证码被拒绝，刷新重试", attempt)
            self.refresh()
        logger.error("验证码尝试 %s 次仍未通过", attempts)
        return False

    def warmup(self) -> Dict[str, float]:
        """提前把模型加载好，避免 07:00 第一次识别时卡 1~2 秒。"""
        timings: Dict[str, float] = {}
        started = time.monotonic()
        try:
            _ = self.recognizer.ocr  # 触发 ddddocr 模型加载
            timings["text_model"] = round(time.monotonic() - started, 2)
            logger.info("字符验证码模型预热完成（%.2fs）", timings["text_model"])
        except Exception as error:  # noqa: BLE001
            logger.warning("验证码模型预热失败: %s", error)

        return timings
