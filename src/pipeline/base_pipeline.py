"""
基础流程模块
提供羽毛球场馆预订的基础流程类
"""
import os
import time
from abc import ABC, abstractmethod
from typing import Optional

from ..config import Config
from ..core.browser import BrowserManager
from ..core.login import LoginManager
from ..core.court_query import QueryManager
from ..core.court_booking import BookingManager
from ..utils.ocr import CaptchaRecognizer
from ..utils.email_notifier import EmailNotifier
from ..utils.logger import setup_logger

logger = setup_logger(__name__)


class BasePipeline(ABC):
    """羽毛球场馆预订基础流程类
    
    提供登录、浏览器管理、场馆导航等基础功能
    子类需要实现 run() 方法来定义具体的业务流程
    """
    
    def __init__(self):
        """初始化基础流程
        
        加载配置文件并初始化各个管理器组件:
        - 浏览器管理器
        - 登录管理器
        - 查询管理器
        - 预订管理器
        - 验证码识别器
        - 邮件通知器
        """
        self.logger = logger
        
        # 加载配置文件（修复路径解析问题）
        logger.info("📄 加载配置文件...")
        project_root = os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )
        )
        config_path = os.path.join(project_root, "config.yaml")
        self.config = Config(config_path)
        
        # 初始化浏览器管理器
        self.browser = BrowserManager(
            headless=self.config.get('login.headless', False)
        )
        
        # 初始化查询和预订管理器
        self.query_manager = QueryManager(self.browser)
        self.booking_manager = BookingManager(self.browser, self.query_manager)
        
        # 初始化验证码识别器和登录管理器
        self.captcha_recognizer = CaptchaRecognizer()
        self.login_manager = LoginManager(self.browser, self.captcha_recognizer)
        
        # 初始化邮件通知器
        self.email_notifier: Optional[EmailNotifier] = self._init_email_notifier()
        
        # 启动浏览器
        logger.info("🌐 启动浏览器...")
        self.browser.start()
    
    def _init_email_notifier(self) -> Optional[EmailNotifier]:
        """初始化邮件通知器
        
        Returns:
            EmailNotifier: 邮件通知器实例，如果配置不完整则返回 None
        """
        smtp_server = self.config.get('email.smtp_server')
        sender_email = self.config.get('email.sender_email')
        sender_password = self.config.get('email.sender_password')
        
        # 检查必需的邮件配置
        if not all([smtp_server, sender_email, sender_password]):
            logger.warning("⚠️  邮件配置不完整，邮件通知功能将不可用")
            return None
        
        return EmailNotifier(
            smtp_server=smtp_server,
            smtp_port=self.config.get('email.smtp_port', 465),
            sender_email=sender_email,
            sender_password=sender_password,
            use_ssl=self.config.get('email.use_ssl', True)
        )
    
    def start_pipeline(self) -> bool:
        """启动流程：登录并进入场馆页面
        
        执行以下步骤:
        1. 使用配置的账号密码登录系统
        2. 自动处理验证码（如果启用）
        3. 导航到指定的场馆类型页面
        4. 等待页面加载完成
        
        Returns:
            bool: 流程是否启动成功
        """
        try:
            # 执行登录
            logger.info("🔐 开始登录...")
            login_success = self.login_manager.login(
                url=self.config.get('login.url'),
                username=self.config.get('user.username'),
                password=self.config.get('user.password'),
                auto_captcha=self.config.get('login.auto_captcha', True),
                max_retry=self.config.get('login.max_retry', 3)
            )
            
            if not login_success:
                logger.error("❌ 登录失败")
                return False
            
            logger.info("✅ 登录成功！")
            
            # 导航到场馆页面
            venue_type = self.config.get('booking.venue_type', '羽毛球')
            logger.info(f"🏸 正在进入{venue_type}场馆...")
            
            if not self.login_manager.navigate_to_venue(venue_type):
                logger.error(f"❌ 进入{venue_type}场馆失败")
                return False
            
            # 等待弹窗消失（通常为5秒自动关闭）
            wait_time = 10
            logger.info(f"⏳ 等待页面加载完成（{wait_time}秒）...")
            time.sleep(wait_time)
            
            logger.info("✅ 成功进入场馆页面，浏览器将保持打开...")
            return True
            
        except Exception as e:
            logger.error(f"❌ 登录过程中发生异常: {e}", exc_info=True)
            self.cleanup()
            return False
    
    def cleanup(self):
        """清理资源
        
        关闭浏览器并释放相关资源
        """
        try:
            if hasattr(self, 'browser') and self.browser:
                logger.info("🧹 清理资源，关闭浏览器...")
                self.browser.close()
        except Exception as e:
            logger.error(f"清理资源时发生错误: {e}")
    
    @abstractmethod
    def run(self) -> bool:
        """执行完整流程（抽象方法）
        
        子类必须实现此方法来定义具体的业务流程
        例如：定时轮询流程、抢票流程等
        
        Returns:
            bool: 流程是否执行成功
        """
        pass
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出，自动清理资源"""
        self.cleanup()