"""
登录模块
"""
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys
from typing import Optional
from .browser import BrowserManager
from ..utils.ocr import CaptchaRecognizer
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

class LoginManager:
    """登录管理器"""
    
    def __init__(self, browser: BrowserManager, ocr: CaptchaRecognizer):
        """
        初始化登录管理器
        
        Args:
            browser: 浏览器管理器
            ocr: OCR识别器
        """
        self.browser = browser
        self.ocr = ocr
    
    def login(self, url: str, username: str, password: str, 
              auto_captcha: bool = True, max_retry: int = 3) -> bool:
        """
        执行登录
        
        Args:
            url: 登录页面URL
            username: 用户名
            password: 密码
            auto_captcha: 是否自动识别验证码
            max_retry: 最大重试次数
            
        Returns:
            是否登录成功
        """
        retry_count = 0
        
        while retry_count < max_retry:
            try:
                # 访问登录页面
                logger.info(f"正在访问登录页面: {url}")
                self.browser.driver.get(url)
                time.sleep(3)
                
                logger.info(f"页面标题: {self.browser.driver.title}")
                
                # 点击"校外用户"按钮
                if not self._click_external_user_button():
                    logger.error("未找到'校外用户'按钮")
                    retry_count += 1
                    continue
                
                # 输入用户名和密码
                if not self._input_credentials(username, password):
                    logger.error("输入用户名或密码失败")
                    retry_count += 1
                    continue
                
                # 处理验证码
                if not self._handle_captcha(auto_captcha):
                    logger.warning("验证码处理失败")
                    retry_count += 1
                    continue
                
                # 点击登录按钮
                if not self._click_login_button():
                    logger.error("点击登录按钮失败")
                    retry_count += 1
                    continue
                
                # 检查登录结果
                time.sleep(5)
                current_url = self.browser.driver.current_url
                logger.info(f"当前URL: {current_url}")
                
                if 'login' not in current_url:
                    logger.info("登录成功！")
                    return True
                else:
                    error_msg = self._get_error_message()
                    if error_msg and '验证码' in error_msg:
                        logger.warning(f"验证码错误，重试 ({retry_count + 1}/{max_retry})")
                        retry_count += 1
                        time.sleep(2)
                        continue
                    else:
                        logger.error(f"登录失败: {error_msg}")
                        self.browser.driver.save_screenshot(f"login_failed_{retry_count}.png")
                        return False
                
            except Exception as e:
                logger.error(f"登录过程出错: {e}", exc_info=True)
                self.browser.driver.save_screenshot("login_error.png")
                retry_count += 1
        
        logger.error(f"已达到最大重试次数 ({max_retry})，登录失败")
        return False
    
    def _click_external_user_button(self) -> bool:
        """点击'校外用户'按钮"""
        try:
            logger.info("正在查找'校外用户'按钮...")
            external_user_btn = self.browser.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), '校外用户')]"))
            )
            self.browser.driver.execute_script("arguments[0].click();", external_user_btn)
            logger.info("已点击'校外用户'按钮")
            time.sleep(2)
            return True
        except TimeoutException:
            logger.error("未找到'校外用户'按钮")
            return False
    
    def _input_credentials(self, username: str, password: str) -> bool:
        """输入用户名和密码"""
        try:
            # 输入用户名
            logger.info("正在输入用户名...")
            username_input = self.browser.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text'], input[name='username']"))
            )
            username_input.clear()
            username_input.send_keys(username)
            logger.info(f"用户名已输入: {username}")
            
            # 输入密码
            logger.info("正在输入密码...")
            password_input = self.browser.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            password_input.clear()
            password_input.send_keys(password)
            logger.info("密码已输入")
            
            return True
        except Exception as e:
            logger.error(f"输入凭据失败: {e}", exc_info=True)
            return False
    
    def _handle_captcha(self, auto_captcha: bool) -> bool:
        """处理验证码"""
        try:
            # 查找验证码输入框
            captcha_input = self._find_captcha_input()
            if not captcha_input:
                logger.info("未检测到验证码")
                return True
            
            logger.info("检测到验证码！")
            
            if auto_captcha:
                # 查找验证码图片
                captcha_img = self._find_captcha_image()
                if not captcha_img:
                    logger.warning("未找到验证码图片")
                    return False
                
                # OCR识别
                logger.info("正在使用OCR识别验证码...")
                time.sleep(0.5)
                captcha_screenshot = captcha_img.screenshot_as_png
                captcha_code = self.ocr.recognize(captcha_screenshot)
                
                if captcha_code and len(captcha_code) >= 4:
                    captcha_input.clear()
                    captcha_input.send_keys(captcha_code)
                    logger.info(f"验证码已自动填入: {captcha_code}")
                    return True
                else:
                    logger.warning("OCR识别失败或验证码长度不足")
                    return False
            else:
                # 手动输入
                captcha_code = input("请输入验证码: ")
                captcha_input.clear()
                captcha_input.send_keys(captcha_code)
                logger.info("验证码已手动输入")
                return True
                
        except Exception as e:
            logger.error(f"验证码处理失败: {e}", exc_info=True)
            return False
    
    def _find_captcha_input(self) -> Optional:
        """查找验证码输入框"""
        selectors = [
            "input[placeholder*='验证码']",
            "input[name*='captcha']",
            "input[name*='code']",
            "input[name='verifyCode']"
        ]
        
        for selector in selectors:
            try:
                elem = self.browser.driver.find_element(By.CSS_SELECTOR, selector)
                logger.debug(f"找到验证码输入框: {selector}")
                return elem
            except NoSuchElementException:
                continue
        
        return None
    
    def _find_captcha_image(self) -> Optional:
        """查找验证码图片"""
        selectors = [
            "div.ivu-form-item-content span[data-v-57a28888] img[data-v-57a28888]",
            "span[data-v-57a28888] > img[data-v-57a28888]",
            "//span[@data-v-57a28888]/img[@data-v-57a28888]",
            "//img[contains(@src, 'blob')]"
        ]
        
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    imgs = self.browser.driver.find_elements(By.XPATH, selector)
                else:
                    imgs = self.browser.driver.find_elements(By.CSS_SELECTOR, selector)
                
                for img in imgs:
                    if img.is_displayed():
                        logger.debug(f"找到验证码图片: {selector}")
                        return img
            except:
                continue
        
        return None
    
    def _click_login_button(self) -> bool:
        """点击登录按钮"""
        try:
            logger.info("正在查找登录按钮...")
            time.sleep(1)
            
            login_btns = self.browser.driver.find_elements(
                By.XPATH, 
                "//button[contains(text(), '登') or contains(@class, 'login') or contains(@class, 'submit')]"
            )
            
            for btn in login_btns:
                if btn.is_displayed():
                    logger.debug(f"找到登录按钮: {btn.text}")
                    self.browser.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    time.sleep(0.5)
                    self.browser.driver.execute_script("arguments[0].click();", btn)
                    logger.info("已点击登录按钮")
                    return True
            
            logger.warning("未找到登录按钮，尝试使用回车键提交")
            return False
            
        except Exception as e:
            logger.error(f"点击登录按钮失败: {e}", exc_info=True)
            return False
    
    def _get_error_message(self) -> Optional[str]:
        """获取错误提示信息"""
        try:
            error_elements = self.browser.driver.find_elements(
                By.CSS_SELECTOR, 
                ".error, .alert, .message, .el-message, .ant-message"
            )
            for elem in error_elements:
                if elem.is_displayed() and elem.text:
                    return elem.text
        except:
            pass
        return None

    def navigate_to_venue(self, venue_name: str = "羽毛球") -> bool:
        """
        导航到指定场馆页面
        
        Args:
            venue_name: 场馆名称（如"羽毛球"）
            
        Returns:
            是否成功进入场馆页面
        """
        try:
            logger.info(f"正在查找场馆: {venue_name}")
            
            # 等待页面加载完成
            time.sleep(2)
            
            # 多种方式查找羽毛球场馆元素
            selectors = [
                # 方式1: 通过data-v属性和文本内容
                f"//dd[@data-v-30e22189][contains(text(), '{venue_name}')]",
                # 方式2: 通过title属性
                f"//dt[@data-v-30e22189][@title='学院路校区-{venue_name}馆']",
                # 方式3: 通过包含羽毛球的任何元素
                f"//*[contains(text(), '{venue_name}')][@data-v-30e22189]",
                # 方式4: 更宽泛的查找
                f"//dl[@data-v-30e22189]//dd[contains(text(), '{venue_name}')]"
            ]
            
            venue_element = None
            used_selector = None
            
            for selector in selectors:
                try:
                    logger.debug(f"尝试选择器: {selector}")
                    elements = self.browser.driver.find_elements(By.XPATH, selector)
                    
                    # 找到第一个可见的元素
                    for elem in elements:
                        if elem.is_displayed():
                            venue_element = elem
                            used_selector = selector
                            logger.info(f"找到场馆元素: {selector}")
                            logger.debug(f"元素文本: {elem.text}")
                            break
                    
                    if venue_element:
                        break
                        
                except NoSuchElementException:
                    logger.debug(f"选择器 {selector} 未找到元素")
                    continue
            
            # 如果上述方法都失败，尝试查找所有包含"羽毛球"的元素
            if not venue_element:
                logger.info("使用备用方案：查找所有包含'羽毛球'的元素")
                all_elements = self.browser.driver.find_elements(By.XPATH, "//*[contains(text(), '羽毛球')]")
                logger.info(f"找到 {len(all_elements)} 个包含'羽毛球'的元素")
                
                for idx, elem in enumerate(all_elements):
                    try:
                        is_displayed = elem.is_displayed()
                        text = elem.text if elem.text else ""
                        tag_name = elem.tag_name
                        logger.debug(f"元素 {idx}: 标签={tag_name}, 可见={is_displayed}, 文本={text}")
                        
                        # 优先选择可点击的元素
                        if is_displayed and (tag_name in ['dd', 'dt', 'a', 'div', 'dl']):
                            venue_element = elem
                            logger.info(f"通过备用方案找到元素: {tag_name}, 文本={text}")
                            break
                    except Exception as e:
                        logger.debug(f"检查元素 {idx} 失败: {e}")
                        continue
            
            if not venue_element:
                logger.error(f"未找到场馆: {venue_name}")
                self.browser.driver.save_screenshot("venue_not_found.png")
                logger.info("已保存截图到 venue_not_found.png")
                return False
            
            # 滚动到元素位置
            self.browser.driver.execute_script("arguments[0].scrollIntoView(true);", venue_element)
            time.sleep(0.5)
            
            # 点击元素
            logger.info(f"正在点击场馆: {venue_name}")
            try:
                # 尝试普通点击
                self.browser.driver.execute_script("arguments[0].click();", venue_element)
                logger.info("进入场馆页面")
            except Exception as e:
                logger.warning(f"进入场馆页面失败: {e}")
                raise
            
            # 等待页面跳转
            time.sleep(3)
            
            # 检查是否成功进入场馆页面
            current_url = self.browser.driver.current_url
            logger.info(f"当前URL: {current_url}")
            
            # 保存场馆页面URL
            self.venue_url = current_url
            
            # 检查页面标题或特定元素来确认是否成功
            page_title = self.browser.driver.title
            logger.info(f"页面标题: {page_title}")
            
            # 如果URL发生了变化或页面标题包含场馆信息，认为成功
            if 'venue' in current_url or venue_name in page_title:
                logger.info(f"成功进入{venue_name}场馆页面")
                return True
            else:
                logger.warning(f"可能未成功进入场馆页面，当前标题: {page_title}")
                self.browser.driver.save_screenshot("venue_page_check.png")
                # 即使不确定，也返回True继续执行
                return True
            
        except Exception as e:
            logger.error(f"导航到场馆失败: {e}", exc_info=True)
            self.browser.driver.save_screenshot("navigate_venue_error.png")
            return False