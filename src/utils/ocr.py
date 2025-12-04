"""
OCR识别工具模块
"""
import os
import time
import ddddocr
from PIL import Image
import io
from typing import Optional
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

class CaptchaRecognizer:
    """验证码识别器"""
    
    def __init__(self, save_dir: str = "captcha_images"):
        """
        初始化识别器
        
        Args:
            save_dir: 验证码图片保存目录
        """
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.save_dir = save_dir
        
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        logger.info(f"OCR识别器初始化成功，图片保存目录: {os.path.abspath(save_dir)}")
    
    def recognize(self, image_data: bytes, save_image: bool = True) -> Optional[str]:
        """
        识别验证码
        
        Args:
            image_data: 图片二进制数据
            save_image: 是否保存图片
            
        Returns:
            识别出的验证码文本
        """
        try:
            # 保存原始图片
            if save_image:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                image_path = os.path.join(self.save_dir, f"captcha_{timestamp}.png")
                with open(image_path, 'wb') as f:
                    f.write(image_data)
                logger.debug(f"验证码图片已保存: {image_path}")
                
                # 显示图片信息
                try:
                    img = Image.open(io.BytesIO(image_data))
                    logger.debug(f"图片尺寸: {img.size}, 模式: {img.mode}")
                except Exception as e:
                    logger.warning(f"无法读取图片信息: {e}")
            
            # OCR识别
            captcha_code = self.ocr.classification(image_data)
            logger.info(f"OCR原始识别结果: {captcha_code}")
            
            # 只保留数字
            captcha_code = ''.join(filter(str.isdigit, captcha_code))
            logger.info(f"过滤后的验证码: {captcha_code}")
            
            # 保存识别结果到文件名
            if save_image and captcha_code:
                result_path = os.path.join(self.save_dir, f"captcha_{timestamp}_result_{captcha_code}.png")
                os.rename(image_path, result_path)
                logger.debug(f"已重命名为: {result_path}")
            
            return captcha_code if len(captcha_code) >= 4 else None
            
        except Exception as e:
            logger.error(f"验证码识别失败: {e}", exc_info=True)
            return None