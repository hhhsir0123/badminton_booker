from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementNotInteractableException
from selenium.webdriver.common.keys import Keys
import time
import ddddocr
from PIL import Image
import io
import base64
import os

class BadmintonBooker:
    def __init__(self):
        """初始化浏览器"""
        chrome_options = Options()
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        # 添加稳定性选项
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, 15)
            # 初始化OCR识别器
            self.ocr = ddddocr.DdddOcr(show_ad=False)
            
            # 创建保存验证码图片的目录
            self.captcha_dir = "captcha_images"
            if not os.path.exists(self.captcha_dir):
                os.makedirs(self.captcha_dir)
            
            print("浏览器启动成功")
            print("OCR识别器初始化成功")
            print(f"验证码图片将保存到: {os.path.abspath(self.captcha_dir)}")
        except Exception as e:
            print(f"初始化失败: {e}")
            raise
    
    def recognize_captcha(self, captcha_element, save_image=True):
        """
        识别验证码图片
        
        Args:
            captcha_element: 验证码图片元素
            save_image: 是否保存原始图片
            
        Returns:
            识别出的验证码文本
        """
        try:
            # 方法1: 直接截图元素（最可靠的方法，支持blob URL）
            print("正在截取验证码图片...")
            captcha_screenshot = captcha_element.screenshot_as_png
            
            # 保存原始图片用于调试
            if save_image:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                image_path = os.path.join(self.captcha_dir, f"captcha_{timestamp}.png")
                with open(image_path, 'wb') as f:
                    f.write(captcha_screenshot)
                print(f"验证码图片已保存到: {image_path}")
                
                # 显示图片信息
                try:
                    img = Image.open(io.BytesIO(captcha_screenshot))
                    print(f"图片尺寸: {img.size}, 模式: {img.mode}")
                except Exception as e:
                    print(f"无法读取图片信息: {e}")
            
            # OCR识别
            captcha_code = self.ocr.classification(captcha_screenshot)
            print(f"OCR原始识别结果: {captcha_code}")
            
            # 保存识别结果到文件名
            if save_image and captcha_code:
                result_path = os.path.join(self.captcha_dir, f"captcha_{timestamp}_result_{captcha_code}.png")
                os.rename(image_path, result_path)
                print(f"已重命名为: {result_path}")
            
            return captcha_code
            
        except Exception as e:
            print(f"截图识别失败: {e}")
            import traceback
            traceback.print_exc()
            
            # 方法2: 通过src属性获取base64图片
            try:
                src = captcha_element.get_attribute('src')
                print(f"验证码src: {src[:100] if src else 'None'}...")
                
                if src and 'base64' in src:
                    # 提取base64数据
                    base64_data = src.split('base64,')[1]
                    image_data = base64.b64decode(base64_data)
                    
                    # 保存base64图片
                    if save_image:
                        timestamp = time.strftime("%Y%m%d_%H%M%S")
                        image_path = os.path.join(self.captcha_dir, f"captcha_base64_{timestamp}.png")
                        with open(image_path, 'wb') as f:
                            f.write(image_data)
                        print(f"Base64图片已保存到: {image_path}")
                    
                    captcha_code = self.ocr.classification(image_data)
                    print(f"OCR识别结果(base64): {captcha_code}")
                    return captcha_code
            except Exception as e2:
                print(f"base64识别失败: {e2}")
                import traceback
                traceback.print_exc()
            
            return None
    
    def login(self, username, password, auto_captcha=True, max_retry=3):
        """
        通过"校外用户"通道登录
        
        Args:
            username: 用户名
            password: 密码
            auto_captcha: 是否自动识别验证码
            max_retry: 验证码识别失败时的最大重试次数
        """
        retry_count = 0
        
        while retry_count < max_retry:
            try:
                # 访问登录页面
                print("正在访问登录页面...")
                self.driver.get('https://ggtypt.cumtb.edu.cn/venue/login')
                time.sleep(3)
                
                print("页面标题:", self.driver.title)
                
                # 尝试多种方式定位"校外用户"按钮
                print("正在查找'校外用户'按钮...")
                try:
                    # 方式1: 通过文本内容
                    external_user_btn = self.wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), '校外用户')]"))
                    )
                except TimeoutException:
                    # 方式2: 通过class或其他属性
                    print("尝试其他方式查找按钮...")
                    external_user_btn = self.wait.until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, "button, a, div[role='button']"))
                    )
                
                print("找到按钮，准备点击...")
                # 使用JavaScript点击，更稳定
                self.driver.execute_script("arguments[0].click();", external_user_btn)
                time.sleep(2)
                
                # 输入用户名
                print("正在输入用户名...")
                username_input = self.wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text'], input[name='username']"))
                )
                self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text'], input[name='username']")))
                username_input.clear()
                username_input.send_keys(username)
                print(f"用户名已输入: {username}")
                
                # 输入密码
                print("正在输入密码...")
                password_input = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
                password_input.clear()
                password_input.send_keys(password)
                print("密码已输入")
                
                # 处理验证码
                captcha_input = None
                captcha_code = None
                
                try:
                    # 查找验证码输入框
                    captcha_selectors = [
                        "input[placeholder*='验证码']",
                        "input[name*='captcha']",
                        "input[name*='code']",
                        "input[name='verifyCode']",
                        "input.captcha-input"
                    ]
                    
                    for selector in captcha_selectors:
                        try:
                            captcha_input = self.driver.find_element(By.CSS_SELECTOR, selector)
                            print(f"找到验证码输入框: {selector}")
                            break
                        except NoSuchElementException:
                            continue
                    
                    if captcha_input:
                        print("\n" + "="*50)
                        print("检测到验证码！")
                        
                        if auto_captcha:
                            # 查找验证码图片 - 根据实际HTML结构精确定位
                            captcha_img = None
                            img_selectors = [
                                # 最精确的选择器，根据你提供的HTML结构
                                "div.ivu-form-item-content span[data-v-57a28888] img[data-v-57a28888]",
                                "span[data-v-57a28888] > img[data-v-57a28888]",
                                "img[data-v-57a28888][src^='blob']",
                                # XPath方式
                                "//span[@data-v-57a28888]/img[@data-v-57a28888]",
                                "//div[@class='ivu-form-item-content']//img[@data-v-57a28888]",
                                # 备用通用选择器
                                "div.ivu-form-item img",
                                "span[data-v-57a28888] img",
                                "img[data-v-57a28888]",
                                "//img[contains(@src, 'blob')]"
                            ]
                            
                            for selector in img_selectors:
                                try:
                                    if selector.startswith("//"):
                                        captcha_imgs = self.driver.find_elements(By.XPATH, selector)
                                        print(f"尝试XPath选择器: {selector}, 找到 {len(captcha_imgs)} 个元素")
                                    else:
                                        captcha_imgs = self.driver.find_elements(By.CSS_SELECTOR, selector)
                                        print(f"尝试CSS选择器: {selector}, 找到 {len(captcha_imgs)} 个元素")
                                    
                                    # 找到第一个可见的验证码图片
                                    for img in captcha_imgs:
                                        if img.is_displayed():
                                            captcha_img = img
                                            print(f"✓ 找到验证码图片: {selector}")
                                            src = img.get_attribute('src')
                                            print(f"图片src: {src[:80] if src else 'None'}...")
                                            
                                            # 获取图片的位置和大小信息
                                            location = img.location
                                            size = img.size
                                            print(f"图片位置: {location}, 大小: {size}")
                                            break
                                    
                                    if captcha_img:
                                        break
                                        
                                except Exception as e:
                                    print(f"选择器 {selector} 失败: {e}")
                                    continue
                            
                            if captcha_img:
                                print("正在使用OCR识别验证码...")
                                time.sleep(0.5)  # 等待图片完全加载
                                captcha_code = self.recognize_captcha(captcha_img, save_image=True)
                                
                                if captcha_code:
                                    # 只保留数字
                                    original_code = captcha_code
                                    captcha_code = ''.join(filter(str.isdigit, captcha_code))
                                    
                                    if original_code != captcha_code:
                                        print(f"过滤后的验证码: {captcha_code} (原始: {original_code})")
                                    
                                    print(f"识别到验证码: {captcha_code} (长度: {len(captcha_code)})")
                                    
                                    # 验证码长度检查
                                    if len(captcha_code) >= 4:
                                        captcha_input.clear()
                                        captcha_input.send_keys(captcha_code)
                                        print("验证码已自动填入")
                                    else:
                                        print(f"识别的验证码长度不足({len(captcha_code)}位)，请手动输入")
                                        print(f"请查看保存的图片: {os.path.abspath(self.captcha_dir)}")
                                        captcha_code = input("请输入验证码: ")
                                        captcha_input.clear()
                                        captcha_input.send_keys(captcha_code)
                                else:
                                    print("OCR识别失败，请手动输入")
                                    print(f"请查看保存的图片: {os.path.abspath(self.captcha_dir)}")
                                    captcha_code = input("请输入验证码: ")
                                    captcha_input.clear()
                                    captcha_input.send_keys(captcha_code)
                            else:
                                print("未找到验证码图片，请手动输入")
                                captcha_code = input("请输入验证码: ")
                                captcha_input.clear()
                                captcha_input.send_keys(captcha_code)
                        else:
                            # 手动输入模式
                            captcha_code = input("请输入验证码: ")
                            captcha_input.clear()
                            captcha_input.send_keys(captcha_code)
                        
                        print("="*50 + "\n")
                        
                except Exception as e:
                    print(f"验证码处理过程出现问题: {e}")
                    import traceback
                    traceback.print_exc()
                
                # 点击登录按钮
                print("正在查找登录按钮...")
                time.sleep(1)
                
                login_clicked = False
                
                try:
                    login_btns = self.driver.find_elements(By.XPATH, "//button[contains(text(), '登') or contains(@class, 'login') or contains(@class, 'submit')]")
                    for btn in login_btns:
                        if btn.is_displayed():
                            print(f"找到登录按钮: {btn.text}")
                            try:
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                                time.sleep(0.5)
                                self.driver.execute_script("arguments[0].click();", btn)
                                print("已点击登录按钮（JavaScript）")
                                login_clicked = True
                                break
                            except Exception as e:
                                print(f"点击按钮失败: {e}")
                                continue
                except Exception as e:
                    print(f"查找登录按钮失败: {e}")
                
                if not login_clicked:
                    print("尝试使用回车键提交表单...")
                    try:
                        if captcha_input:
                            captcha_input.send_keys(Keys.RETURN)
                        else:
                            password_input.send_keys(Keys.RETURN)
                        login_clicked = True
                    except Exception as e:
                        print(f"回车提交失败: {e}")
                
                if not login_clicked:
                    print("警告: 未能触发登录操作")
                    retry_count += 1
                    continue
                
                print("已触发登录")
                
                # 等待登录完成
                print("等待登录结果...")
                time.sleep(5)
                
                # 检查是否登录成功
                current_url = self.driver.current_url
                print(f"当前URL: {current_url}")
                
                if 'login' not in current_url:
                    print("\n" + "="*50)
                    print("登录成功！")
                    print("="*50 + "\n")
                    return True
                else:
                    print("\n登录失败，仍在登录页面")
                    
                    # 检查错误提示
                    try:
                        error_elements = self.driver.find_elements(By.CSS_SELECTOR, ".error, .alert, .message, .el-message, .ant-message")
                        for elem in error_elements:
                            if elem.is_displayed() and elem.text:
                                error_msg = elem.text
                                print(f"错误信息: {error_msg}")
                                
                                # 如果是验证码错误，重试
                                if '验证码' in error_msg or 'captcha' in error_msg.lower():
                                    retry_count += 1
                                    print(f"验证码错误，重试 ({retry_count}/{max_retry})...")
                                    time.sleep(2)
                                    break
                    except Exception as e:
                        print(f"获取错误信息失败: {e}")
                    
                    self.driver.save_screenshot(f"login_failed_{retry_count}.png")
                    print(f"已保存截图到 login_failed_{retry_count}.png")
                    
                    # 如果不是验证码错误，直接返回失败
                    if retry_count == 0:
                        return False
                
            except TimeoutException as e:
                print(f"超时错误: 无法找到元素")
                self.driver.save_screenshot("error_screenshot.png")
                print("已保存错误截图到 error_screenshot.png")
                return False
            except Exception as e:
                print(f"登录失败: {type(e).__name__}: {str(e)}")
                import traceback
                traceback.print_exc()
                self.driver.save_screenshot("error_screenshot.png")
                print("已保存错误截图到 error_screenshot.png")
                return False
        
        print(f"已达到最大重试次数 ({max_retry})，登录失败")
        return False
    
    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            print("浏览器已关闭")

def main():
    booker = None
    
    try:
        booker = BadmintonBooker()
        
        # 获取用户名和密码
        username = input("请输入用户名: ")
        password = input("请输入密码: ")
        
        # 询问是否自动识别验证码
        auto_mode = input("是否自动识别验证码？(y/n，默认y): ").strip().lower()
        auto_captcha = auto_mode != 'n'
        
        # 执行登录
        if booker.login(username, password, auto_captcha=auto_captcha):
            print("登录成功，浏览器将保持打开状态...")
            print("您可以继续在浏览器中操作")
            input("完成后按回车键关闭浏览器...")
        else:
            print("登录失败")
            input("按回车键关闭浏览器...")
            
    except Exception as e:
        print(f"程序执行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if booker:
            booker.close()

def test_mode():
    booker = None
    
    try:
        booker = BadmintonBooker()
        
        # 获取用户名和密码
        username = '17396154630'
        password = 'hhhSir012345'
        
        # 询问是否自动识别验证码
        auto_mode = 'y'
        auto_captcha = auto_mode != 'n'
        
        # 执行登录
        if booker.login(username, password, auto_captcha=auto_captcha):
            print("登录成功，浏览器将保持打开状态...")
            print("您可以继续在浏览器中操作")
            input("完成后按回车键关闭浏览器...")
        else:
            print("登录失败")
            input("按回车键关闭浏览器...")
            
    except Exception as e:
        print(f"程序执行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if booker:
            booker.close()

if __name__ == "__main__":
    test_mode()