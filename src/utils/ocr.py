"""
OCR识别工具模块

ddddocr 采用懒加载：没装依赖时整个程序仍然可以启动、可以跑选场预览，
只有在真正需要识别验证码时才会报出清晰的错误信息。
"""
import io
import os
import time
from typing import Optional

from ..utils.logger import setup_logger

logger = setup_logger(__name__)


def load_ddddocr():
    """按需导入 ddddocr，缺依赖时给出可执行的提示。"""
    try:
        import ddddocr  # noqa: WPS433  (运行期按需导入)
    except ImportError as error:
        raise RuntimeError("缺少验证码识别依赖 ddddocr，请执行: pip install ddddocr") from error
    return ddddocr


class CaptchaRecognizer:
    """验证码识别器"""

    def __init__(self, save_dir: str = "captcha_images"):
        # 不在构造时建目录：只要不真的识别验证码，就不应该往仓库里写文件
        self.save_dir = save_dir
        self._ocr = None
        logger.info("OCR识别器就绪（首次识别时加载模型）")

    @property
    def ocr(self):
        if self._ocr is None:
            self._ocr = load_ddddocr().DdddOcr(show_ad=False)
            logger.info("ddddocr 模型已加载")
        return self._ocr

    def recognize(self, image_data: bytes, save_image: bool = True) -> Optional[str]:
        """识别数字验证码，返回 4 位以上数字或 None。"""
        try:
            stamp = None
            image_path = None
            if save_image:
                os.makedirs(self.save_dir, exist_ok=True)
                stamp = time.strftime("%Y%m%d_%H%M%S")
                image_path = os.path.join(self.save_dir, f"captcha_{stamp}.png")
                with open(image_path, "wb") as handle:
                    handle.write(image_data)

            code = self.ocr.classification(image_data)
            logger.info("OCR原始识别结果: %s", code)
            code = "".join(filter(str.isdigit, code or ""))
            logger.info("过滤后的验证码: %s", code)

            if save_image and image_path and code:
                os.replace(image_path, os.path.join(self.save_dir, f"captcha_{stamp}_result_{code}.png"))

            return code if len(code) >= 4 else None
        except Exception as error:  # noqa: BLE001 - 识别失败不应打断流程
            logger.error("验证码识别失败: %s", error, exc_info=True)
            return None


def image_size(image_data: bytes):
    """调试用：读取图片尺寸，Pillow 缺失时返回 None。"""
    try:
        from PIL import Image

        return Image.open(io.BytesIO(image_data)).size
    except Exception:  # noqa: BLE001
        return None
