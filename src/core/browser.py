"""
浏览器管理模块
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from typing import Optional
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

class BrowserManager:
    """浏览器管理器"""
    
    def __init__(self, headless: bool = False):
        """
        初始化浏览器
        
        Args:
            headless: 是否使用无头模式
        """
        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None
        self.headless = headless
    
    def start(self, timeout: int = 15):
        """
        启动浏览器
        
        Args:
            timeout: 默认等待超时时间
        """
        chrome_options = Options()
        
        if self.headless:
            chrome_options.add_argument('--headless')
        else:
            chrome_options.add_argument('--start-maximized')
        
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.wait = WebDriverWait(self.driver, timeout)
            logger.info("浏览器启动成功")
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}", exc_info=True)
            raise
    
    def close(self):
        """关闭浏览器"""
        if self.driver:
            self.driver.quit()
            logger.info("浏览器已关闭")
    
    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()