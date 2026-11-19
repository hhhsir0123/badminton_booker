"""点选文字验证码（AJ-Captcha clickWord）识别。

流程：定位字符 → 倾斜校正 → OCR 识别（带置信度）→ 按目标词表匹配
      → 没认出来的目标字用「字形模板」兜底 → 返回点击坐标。

实测（15 张真实下单验证码，2026-11-19）：
- 纯 OCR 匹配：三个字全中 8/15，中位 312ms
- 加模板兜底：三个字全中 11/15，中位 359ms
- 置信度门控（阈值 0.5~0.6）：一次都不触发，无收益；调高只会减少覆盖
- 全量角度/尺度（7 角度 × 3 尺度）：只多救 1 张，耗时 5s（16 倍），不做

点击必须走组件自己的方法（踩过坑）：
    vm.canvasClick({offsetX: x * imgWidth / 310, offsetY: y * imgHeight / 155})
前端读的是 event.offsetX，合成 MouseEvent 不可靠；pointTransfrom() 会换算回
310×155 的服务器坐标系。

依赖：ddddocr（检测）+ rapidocr-onnxruntime（中文识别）+ opencv-python。
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

def _available_fonts() -> List[str]:
    """按平台挑可用的中文字体；找不到就返回空列表（模板兜底自动降级）。"""
    import os
    import sys

    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    return [path for path in candidates if os.path.exists(path)]


FONTS = _available_fonts()
TEMPLATE_SIZE = 48
TEMPLATE_PAD = 12
TEMPLATE_ANGLES = (-30, -20, -10, 0, 10, 20, 30)
_TEMPLATES: Dict[str, List[np.ndarray]] = {}


def _prepare_mask(mask: np.ndarray, size: int = TEMPLATE_SIZE) -> np.ndarray:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return np.zeros((size, size), np.uint8)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    height, width = crop.shape
    scale = size / max(height, width)
    crop = cv2.resize(crop, (max(1, int(width * scale)), max(1, int(height * scale))),
                      interpolation=cv2.INTER_AREA)
    canvas = np.zeros((size, size), np.uint8)
    top = (size - crop.shape[0]) // 2
    left = (size - crop.shape[1]) // 2
    canvas[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
    return canvas


def _rotate(mask: np.ndarray, angle: float) -> np.ndarray:
    height, width = mask.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(mask, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)


def templates_for(char: str) -> List[np.ndarray]:
    if char in _TEMPLATES:
        return _TEMPLATES[char]
    items = []
    for font_path in FONTS:
        try:
            font = ImageFont.truetype(font_path, 96)
        except Exception:  # noqa: BLE001
            continue
        image = Image.new("L", (160, 160), 255)
        ImageDraw.Draw(image).text((10, 10), char, font=font, fill=0)
        mask = ((np.array(image) < 128).astype(np.uint8)) * 255
        for angle in TEMPLATE_ANGLES:
            items.append(_prepare_mask(_rotate(mask, angle)))
    _TEMPLATES[char] = items
    return items


def shape_score(mask: np.ndarray, templates: List[np.ndarray]) -> float:
    if mask is None or not templates:
        return 0.0
    canvas = np.zeros((TEMPLATE_SIZE + TEMPLATE_PAD * 2, TEMPLATE_SIZE + TEMPLATE_PAD * 2), np.uint8)
    canvas[TEMPLATE_PAD:TEMPLATE_PAD + TEMPLATE_SIZE, TEMPLATE_PAD:TEMPLATE_PAD + TEMPLATE_SIZE] = mask
    best = 0.0
    for template in templates:
        result = cv2.matchTemplate(canvas, template, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


class ClickWordSolver:
    def __init__(self, min_confidence: float = 0.6, fallback_score: float = 0.7,
                 enable_fallback: bool = False, provider=None,
                 angles: Tuple[int, ...] = (-10, 0, 10), padding: int = 8):
        self.min_confidence = min_confidence
        self.fallback_score = fallback_score
        # 实测（2026-11-19 乒乓球真机）：兜底触发的 2 次全部配错框，默认关闭。
        # 宁可"这次不点"，也不要"点错一个字"——点错会浪费一次机会并可能触发风控。
        self.enable_fallback = enable_fallback
        # 可选：图鉴等打码平台 provider，优先使用，失败回落本地 OCR
        self.provider = provider
        self.angles = angles
        self.padding = padding
        self._detector = None
        self._engine = None

    @property
    def detector(self):
        if self._detector is None:
            from ddddocr import DdddOcr

            self._detector = DdddOcr(det=True, show_ad=False)
            logger.info("点选检测模型已加载")
        return self._detector

    @property
    def engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
            logger.info("中文识别模型已加载")
        return self._engine

    def warmup(self) -> bool:
        try:
            _ = self.detector
            _ = self.engine
            _ = templates_for("的")
            return True
        except Exception as error:  # noqa: BLE001
            logger.warning("验证码模型预热失败: %s", error)
            return False

    # ------------------------------------------------------------- 识别

    def _recognize(self, image) -> Tuple[str, float]:
        best_text, best_conf = "", 0.0
        for angle in self.angles:
            buffer = io.BytesIO()
            image.rotate(angle, expand=True, fillcolor="white").save(buffer, format="PNG")
            result, _ = self.engine(buffer.getvalue(), use_det=False, use_cls=False, use_rec=True)
            if not result:
                continue
            text, conf = "", 0.0
            for value in result[0]:
                if isinstance(value, str) and not text:
                    text = value
                elif isinstance(value, (int, float)):
                    conf = float(value)
            if text and conf > best_conf:
                best_text, best_conf = text, conf
        return best_text, best_conf

    @staticmethod
    def _deskew(crop):
        gray = cv2.cvtColor(np.array(crop.convert("RGB")), cv2.COLOR_RGB2GRAY)
        binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 20:
            return crop
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        if abs(angle) > 40:
            return crop
        return crop.rotate(angle, expand=True, fillcolor="white")

    def read_chars(self, png: bytes) -> List[Dict]:
        image = Image.open(io.BytesIO(png))
        items = []
        for box in self.detector.detection(png):
            x1, y1, x2, y2 = box
            width, height = x2 - x1, y2 - y1
            if width < 14 or height < 14 or width > 90 or height > 90:
                continue
            if not (0.3 <= width / max(1, height) <= 3.0):
                continue
            crop = image.crop((max(0, x1 - self.padding), max(0, y1 - self.padding),
                               min(image.width, x2 + self.padding), min(image.height, y2 + self.padding)))
            upright = self._deskew(crop)
            text, conf = self._recognize(upright)
            gray = cv2.cvtColor(np.array(upright.convert("RGB")), cv2.COLOR_RGB2GRAY)
            binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            items.append({
                "char": text, "conf": conf,
                "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                "mask": _prepare_mask(binary),
            })
        return items

    # ------------------------------------------------------------- 匹配

    @staticmethod
    def match(chars: List[str], centers: List[Tuple[int, int]], words: List[str]):
        used, points = set(), []
        for word in words:
            for index, char in enumerate(chars):
                if index in used or char != word:
                    continue
                used.add(index)
                points.append(centers[index])
                break
        return points if len(points) == len(words) else None

    def solve(self, png: bytes, words: List[str]) -> Optional[List[Tuple[int, int]]]:
        if self.provider is not None:
            try:
                points = self.provider.solve(png, words=words)
                if len(points) == len(words):
                    logger.info("打码平台返回坐标: %s", points)
                    return [(int(x), int(y)) for x, y in points]
                logger.warning("打码平台返回 %s 个坐标，目标 %s 个，回落本地", len(points), len(words))
            except Exception as error:  # noqa: BLE001
                logger.warning("打码平台失败，回落本地 OCR: %s", error)

        items = self.read_chars(png)
        used, points, fallbacks = set(), [], 0
        for word in words:
            hit = None
            for index, item in enumerate(items):
                if index in used or item["char"] != word or item["conf"] < self.min_confidence:
                    continue
                hit = index
                break
            if hit is None and self.enable_fallback:
                best_index, best_score = None, 0.0
                templates = templates_for(word)
                for index, item in enumerate(items):
                    if index in used:
                        continue
                    score = shape_score(item["mask"], templates)
                    if score > best_score:
                        best_index, best_score = index, score
                if best_index is not None and best_score >= self.fallback_score:
                    hit = best_index
                    fallbacks += 1
            if hit is None:
                logger.warning("目标字 %s 没认出来，放弃本次点选", word)
                return None
            used.add(hit)
            points.append(items[hit]["center"])
        logger.info("点选识别: 目标=%s 字符=%s 模板兜底=%s",
                    words, [item["char"] for item in items], fallbacks)
        return points

    @staticmethod
    def to_canvas(points, image_size, img_width: int, img_height: int):
        """310×155 图片坐标 → 画布坐标（给 vm.canvasClick 的 offsetX/offsetY）。"""
        width, height = image_size
        return [[x * img_width / width, y * img_height / height] for x, y in points]
