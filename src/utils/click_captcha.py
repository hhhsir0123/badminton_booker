"""
点选验证码处理模块 - 使用ddddocr成熟方案
ddddocr专门针对各类验证码优化，识别率高
"""
import time
import re
from io import BytesIO
from typing import List, Tuple, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
import ddddocr
from .logger import setup_logger

logger = setup_logger(__name__)


class ClickCaptchaHandler:
    """点选验证码处理器 - 使用ddddocr"""
    
    def __init__(self, browser):
        """初始化验证码处理器"""
        self.browser = browser
        
        try:
            # 初始化ddddocr - 使用beta版本的点选功能
            self.ocr = ddddocr.DdddOcr(det=False, ocr=True, show_ad=False)
            self.det = ddddocr.DdddOcr(det=True, show_ad=False)
            logger.info("ddddocr初始化成功")
        except Exception as e:
            logger.error(f"初始化ddddocr失败: {e}")
            self.ocr = None
            self.det = None
    
    def handle_click_captcha(self, max_retry: int = 50) -> bool:
        """处理点选验证码"""
        if not self.ocr or not self.det:
            logger.error("OCR未初始化")
            return False
        
        for retry in range(max_retry):
            try:
                logger.info(f"尝试处理验证码 (第 {retry + 1}/{max_retry} 次)")
                
                if not self._check_click_captcha_exists():
                    logger.info("未检测到点选验证码")
                    return True
                
                hint_text = self._get_hint_text()
                if not hint_text:
                    logger.warning("无法获取提示文字")
                    self._refresh_captcha()
                    time.sleep(0.3)
                    continue
                
                logger.info(f"验证码提示: {hint_text}")
                
                target_chars = self._extract_target_chars(hint_text)
                if not target_chars:
                    logger.warning("无法提取目标文字")
                    self._refresh_captcha()
                    time.sleep(0.3)
                    continue
                
                logger.info(f"目标文字: {target_chars}")
                
                captcha_element, captcha_image = self._get_captcha_image()
                if not captcha_image or not captcha_element:
                    logger.warning("无法获取验证码图片")
                    self._refresh_captcha()
                    time.sleep(0.3)
                    continue
                
                # 使用ddddocr检测+识别
                positions = self._recognize_with_ddddocr(captcha_image, target_chars)
                
                if not positions or len(positions) != len(target_chars):
                    logger.warning(f"识别失败: 需要{len(target_chars)}个，识别到{len(positions)}个")
                    self._refresh_captcha()
                    time.sleep(0.3)
                    continue
                
                if self._click_positions(captcha_element, positions):
                    time.sleep(0.3)
                    
                    if not self._check_click_captcha_exists():
                        logger.info("✓ 验证码处理成功！")
                        return True
                    else:
                        logger.warning("验证码仍存在，继续重试")
                        self._refresh_captcha()
                        time.sleep(0.3)
                else:
                    self._refresh_captcha()
                    time.sleep(0.3)
                
            except Exception as e:
                logger.error(f"处理验证码出错: {e}", exc_info=True)
                self._refresh_captcha()
                time.sleep(0.3)
        
        logger.error(f"达到最大重试次数 ({max_retry})")
        return False
    
    def _recognize_with_ddddocr(self, image_bytes: bytes, target_chars: List[str]) -> List[Tuple[int, int]]:
        """使用ddddocr进行检测和识别"""
        try:
            timestamp = int(time.time())
            
            # 保存调试图片
            with open(f"captcha_{timestamp}.png", 'wb') as f:
                f.write(image_bytes)
            
            # 1. 使用det模型检测文字位置
            det_result = self.det.detection(image_bytes)
            logger.info(f"检测到 {len(det_result)} 个文字框")
            
            if not det_result:
                return []
            
            # 2. 对每个检测框进行OCR识别
            from PIL import Image
            img = Image.open(BytesIO(image_bytes))
            
            char_locations = []
            for i, box in enumerate(det_result):
                x1, y1, x2, y2 = box
                
                # 裁剪文字区域
                cropped = img.crop((x1, y1, x2, y2))
                
                # 转为字节
                buf = BytesIO()
                cropped.save(buf, format='PNG')
                cropped_bytes = buf.getvalue()
                
                # OCR识别
                text = self.ocr.classification(cropped_bytes)
                
                # 计算中心点
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                logger.info(f"  [{i+1}] 位置({center_x},{center_y}) 识别: '{text}'")
                
                char_locations.append({
                    'text': text,
                    'center': (center_x, center_y),
                    'box': box
                })
            
            # 3. 匹配目标字符
            positions = []
            used_indices = set()
            
            for target in target_chars:
                best_match = None
                best_idx = None
                
                for i, item in enumerate(char_locations):
                    if i in used_indices:
                        continue
                    
                    # 精确匹配或包含匹配
                    if target == item['text'] or target in item['text']:
                        best_match = item
                        best_idx = i
                        break
                
                if best_match:
                    positions.append(best_match['center'])
                    used_indices.add(best_idx)
                    logger.info(f"✓ '{target}' 匹配到 {best_match['center']}")
                else:
                    logger.warning(f"✗ '{target}' 未找到")
                    return []
            
            return positions
            
        except Exception as e:
            logger.error(f"ddddocr识别失败: {e}", exc_info=True)
            return []
    
    def _check_click_captcha_exists(self) -> bool:
        """检查是否存在点选验证码"""
        try:
            xpath_queries = [
                "//*[contains(text(), '请依次点击')]",
                "//*[contains(text(), '请点击')]",
                "//div[contains(@class, 'verify')]",
            ]
            
            for xpath in xpath_queries:
                try:
                    elements = self.browser.driver.find_elements(By.XPATH, xpath)
                    if any(elem.is_displayed() for elem in elements):
                        return True
                except:
                    continue
            return False
        except:
            return False
    
    def _get_hint_text(self) -> Optional[str]:
        """获取验证码提示文字"""
        try:
            xpath_queries = [
                "//*[contains(text(), '请依次点击')]",
                "//*[contains(text(), '请点击')]",
            ]
            
            for xpath in xpath_queries:
                try:
                    elements = self.browser.driver.find_elements(By.XPATH, xpath)
                    for elem in elements:
                        if elem.is_displayed():
                            return elem.text.strip()
                except:
                    continue
            return None
        except:
            return None
    
    def _extract_target_chars(self, hint_text: str) -> List[str]:
        """从提示文字中提取需要点击的文字"""
        match = re.search(r'【(.+?)】', hint_text)
        if match:
            chars_str = match.group(1)
            chars = re.split(r'[,，、\s]+', chars_str)
            return [c.strip() for c in chars if c.strip()]
        return []
    
    def _get_captcha_image(self) -> Tuple[Optional[object], Optional[bytes]]:
        """获取验证码图片元素和图片数据"""
        try:
            selectors = [
                "div.verify-img-panel img",
                "div[class*='verify'] img",
            ]
            
            for selector in selectors:
                try:
                    elements = self.browser.driver.find_elements(By.CSS_SELECTOR, selector)
                    for elem in elements:
                        if elem.is_displayed():
                            return elem, elem.screenshot_as_png
                except:
                    continue
            return None, None
        except:
            return None, None
    
    def _click_positions(self, captcha_element, positions: List[Tuple[int, int]]) -> bool:
        """按顺序点击指定位置"""
        try:
            size = captcha_element.size
            actions = ActionChains(self.browser.driver)
            
            for idx, (x, y) in enumerate(positions):
                offset_x = x - size['width'] // 2
                offset_y = y - size['height'] // 2
                
                logger.info(f"点击 {idx+1}: ({x},{y}) -> 偏移({offset_x},{offset_y})")
                
                actions.move_to_element_with_offset(captcha_element, offset_x, offset_y).click().perform()
                time.sleep(0.3)
            
            return True
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return False
    
    def _refresh_captcha(self):
        """刷新验证码"""
        try:
            selectors = [
                ("css", "i.iconfont.icon-refresh"),
                ("xpath", "//i[contains(@class, 'icon-refresh')]"),
            ]
            
            for sel_type, selector in selectors:
                try:
                    if sel_type == "xpath":
                        elem = self.browser.driver.find_element(By.XPATH, selector)
                    else:
                        elem = self.browser.driver.find_element(By.CSS_SELECTOR, selector)
                    
                    if elem.is_displayed():
                        try:
                            elem.click()
                        except:
                            self.browser.driver.execute_script("arguments[0].click();", elem)
                        logger.info("已刷新验证码")
                        time.sleep(0.5)
                        return True
                except:
                    continue
            return False
        except:
            return False