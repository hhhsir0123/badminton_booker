"""
点选验证码处理模块 - 使用 ddddocr 成熟方案

ddddocr 专门针对各类验证码优化，识别率高
使用 det 模型检测文字位置，使用 ocr 模型识别文字内容
"""
import time
import re
from io import BytesIO
from typing import List, Tuple, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from PIL import Image
import ddddocr

from .logger import setup_logger

logger = setup_logger(__name__)


class ClickCaptchaHandler:
    """点选验证码处理器
    
    使用 ddddocr 进行验证码检测和识别：
    1. 使用 det 模型检测验证码中的文字区域
    2. 使用 ocr 模型识别每个区域的文字内容
    3. 匹配目标文字并按顺序点击
    
    Attributes:
        browser: 浏览器管理器实例
        ocr: ddddocr OCR 识别器
        det: ddddocr 文字检测器
    """
    
    def __init__(self, browser):
        """初始化验证码处理器
        
        Args:
            browser: 浏览器管理器实例
        """
        self.browser = browser
        
        try:
            # 初始化 ddddocr - 使用 beta 版本的点选功能
            self.ocr = ddddocr.DdddOcr(det=False, ocr=True, show_ad=False)
            self.det = ddddocr.DdddOcr(det=True, show_ad=False)
            logger.info("✅ ddddocr 初始化成功")
        except Exception as e:
            logger.error(f"❌ 初始化 ddddocr 失败: {e}")
            self.ocr = None
            self.det = None
    
    def handle_click_captcha(self, max_retry: int = 50) -> bool:
        """处理点选验证码
        
        循环尝试识别并点击验证码，直到成功或达到最大重试次数
        
        Args:
            max_retry: 最大重试次数，默认 50 次
            
        Returns:
            bool: 验证码是否处理成功
        """
        if not self.ocr or not self.det:
            logger.error("❌ OCR 未初始化")
            return False
        
        for retry in range(max_retry):
            try:
                logger.info(f"🔄 尝试处理验证码 (第 {retry + 1}/{max_retry} 次)")
                
                # 检查验证码是否存在
                if not self._check_click_captcha_exists():
                    logger.info("✅ 未检测到点选验证码")
                    return True
                
                # 获取提示文字
                hint_text = self._get_hint_text()
                if not hint_text:
                    logger.warning("⚠️  无法获取提示文字，刷新验证码")
                    self._refresh_captcha()
                    continue
                
                logger.info(f"💬 验证码提示: {hint_text}")
                
                # 提取目标字符
                target_chars = self._extract_target_chars(hint_text)
                if not target_chars:
                    logger.warning("⚠️  无法提取目标文字，刷新验证码")
                    self._refresh_captcha()
                    continue
                
                logger.info(f"🎯 目标文字: {target_chars}")
                
                # 获取验证码图片
                captcha_element, captcha_image = self._get_captcha_image()
                if not captcha_image or not captcha_element:
                    logger.warning("⚠️  无法获取验证码图片，刷新验证码")
                    self._refresh_captcha()
                    continue
                
                # 使用 ddddocr 检测 + 识别
                positions = self._recognize_with_ddddocr(captcha_image, target_chars)
                
                if not positions or len(positions) != len(target_chars):
                    logger.warning(
                        f"⚠️  识别失败: 需要 {len(target_chars)} 个，识别到 {len(positions)} 个"
                    )
                    self._refresh_captcha()
                    continue
                
                # 点击识别到的位置
                if self._click_positions(captcha_element, positions):
                    time.sleep(1)
                    
                    # 验证是否成功
                    if not self._check_click_captcha_exists():
                        logger.info("✅ 验证码处理成功！")
                        return True
                    else:
                        logger.warning("⚠️  验证码仍存在，继续重试")
                        self._refresh_captcha()
                else:
                    self._refresh_captcha()
                
            except Exception as e:
                logger.error(f"❌ 处理验证码出错: {e}", exc_info=True)
                self._refresh_captcha()
        
        logger.error(f"❌ 达到最大重试次数 ({max_retry})")
        return False
    
    def _recognize_with_ddddocr(
        self, 
        image_bytes: bytes, 
        target_chars: List[str]
    ) -> List[Tuple[int, int]]:
        """使用 ddddocr 进行检测和识别
        
        流程：
        1. 使用 det 模型检测文字位置
        2. 对每个检测框进行 OCR 识别
        3. 匹配目标字符并返回位置
        
        Args:
            image_bytes: 验证码图片的字节数据
            target_chars: 需要点击的目标字符列表
            
        Returns:
            List[Tuple[int, int]]: 目标字符的位置列表 [(x1, y1), (x2, y2), ...]
        """
        try:
            timestamp = int(time.time())
            
            # 保存调试图片
            with open(f"captcha_{timestamp}.png", 'wb') as f:
                f.write(image_bytes)
            
            # 步骤 1: 使用 det 模型检测文字位置
            det_result = self.det.detection(image_bytes)
            logger.info(f"🔍 检测到 {len(det_result)} 个文字框")
            
            if not det_result:
                logger.warning("⚠️  未检测到任何文字框")
                return []
            
            # 步骤 2: 对每个检测框进行 OCR 识别
            img = Image.open(BytesIO(image_bytes))
            char_locations = []
            
            for i, box in enumerate(det_result):
                x1, y1, x2, y2 = box
                
                # 裁剪文字区域
                cropped = img.crop((x1, y1, x2, y2))
                
                # 转为字节流
                buf = BytesIO()
                cropped.save(buf, format='PNG')
                cropped_bytes = buf.getvalue()
                
                # OCR 识别
                text = self.ocr.classification(cropped_bytes)
                
                # 计算中心点坐标
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                logger.info(f"  [{i + 1}] 位置 ({center_x}, {center_y}) 识别: '{text}'")
                
                char_locations.append({
                    'text': text,
                    'center': (center_x, center_y),
                    'box': box
                })
            
            # 步骤 3: 匹配目标字符
            positions = []
            used_indices = set()
            
            for target in target_chars:
                best_match = None
                best_idx = None
                
                # 在未使用的字符中查找匹配
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
                    logger.info(f"✅ '{target}' 匹配到位置 {best_match['center']}")
                else:
                    logger.warning(f"❌ '{target}' 未找到匹配")
                    return []
            
            return positions
            
        except Exception as e:
            logger.error(f"❌ ddddocr 识别失败: {e}", exc_info=True)
            return []
    
    def _check_click_captcha_exists(self) -> bool:
        """检查是否存在点选验证码
        
        通过多个 XPath 查询验证码相关元素
        
        Returns:
            bool: 验证码是否存在
        """
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
        """获取验证码提示文字
        
        提示文字通常格式为："请依次点击【文字1、文字2】"
        
        Returns:
            Optional[str]: 提示文字，如果未找到则返回 None
        """
        try:
            xpath_queries = [
                "//*[contains(text(), '请依次点击')]",
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
        """从提示文字中提取需要点击的文字
        
        提取 【】 中的文字并按分隔符（逗号、顿号等）分割
        
        Args:
            hint_text: 提示文字，如 "请依次点击【文字1、文字2】"
            
        Returns:
            List[str]: 目标字符列表，如 ['文字1', '文字2']
        """
        match = re.search(r'【(.+?)】', hint_text)
        if match:
            chars_str = match.group(1)
            # 支持多种分隔符：逗号、顿号、空格等
            chars = re.split(r'[,，、\s]+', chars_str)
            return [c.strip() for c in chars if c.strip()]
        return []
    
    def _get_captcha_image(self) -> Tuple[Optional[object], Optional[bytes]]:
        """获取验证码图片元素和图片数据
        
        Returns:
            Tuple[Optional[object], Optional[bytes]]: 
                (图片元素, 图片字节数据)，如果未找到则返回 (None, None)
        """
        try:
            selectors = [
                "div.verify-img-panel img",
                "div[class*='verify'] img",
            ]
            
            for selector in selectors:
                try:
                    elements = self.browser.driver.find_elements(
                        By.CSS_SELECTOR, 
                        selector
                    )
                    for elem in elements:
                        if elem.is_displayed():
                            return elem, elem.screenshot_as_png
                except:
                    continue
            
            return None, None
        except:
            return None, None
    
    def _click_positions(
        self, 
        captcha_element, 
        positions: List[Tuple[int, int]]
    ) -> bool:
        """按顺序点击指定位置
        
        Args:
            captcha_element: 验证码图片元素
            positions: 需要点击的位置列表 [(x1, y1), (x2, y2), ...]
            
        Returns:
            bool: 点击是否成功
        """
        try:
            size = captcha_element.size
            actions = ActionChains(self.browser.driver)
            
            for idx, (x, y) in enumerate(positions):
                # 计算相对于元素中心的偏移量
                offset_x = x - size['width'] // 2
                offset_y = y - size['height'] // 2
                
                logger.info(
                    f"👆 点击 {idx + 1}: 绝对位置 ({x}, {y}) -> "
                    f"偏移量 ({offset_x}, {offset_y})"
                )
                
                # 移动到偏移位置并点击
                actions.move_to_element_with_offset(
                    captcha_element, 
                    offset_x, 
                    offset_y
                ).click().perform()
                
                # 每次点击之间短暂等待
                time.sleep(0.3)
            
            return True
        except Exception as e:
            logger.error(f"❌ 点击失败: {e}")
            return False
    
    def _refresh_captcha(self):
        """刷新验证码
        
        查找并点击刷新按钮，通常是刷新图标
        
        Returns:
            bool: 刷新是否成功
        """
        try:
            selectors = [
                ("xpath", "//i[contains(@class, 'icon-refresh')]"),
            ]
            
            for sel_type, selector in selectors:
                try:
                    # 根据选择器类型定位元素
                    if sel_type == "xpath":
                        elem = self.browser.driver.find_element(By.XPATH, selector)
                    else:
                        elem = self.browser.driver.find_element(
                            By.CSS_SELECTOR, 
                            selector
                        )
                    
                    if elem.is_displayed():
                        try:
                            # 优先使用普通点击
                            elem.click()
                        except:
                            # 如果普通点击失败，使用 JavaScript 点击
                            self.browser.driver.execute_script(
                                "arguments[0].click();", 
                                elem
                            )
                        
                        logger.info("🔄 已刷新验证码")
                        time.sleep(0.5)
                        return True
                except:
                    continue
            
            return False
        except:
            return False