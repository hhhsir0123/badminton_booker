"""
主程序入口
"""
import sys
import os
import time

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import Config
from src.core.browser import BrowserManager
from src.core.login import LoginManager
from src.core.court_query import QueryManager
from src.core.court_booking import BookingManager
from src.utils.ocr import CaptchaRecognizer
from src.utils.email_notifier import EmailNotifier
from src.utils.logger import setup_logger

logger = setup_logger('main', log_file='logs/main.log')

def main():
    """主函数"""
    try:
        # 加载配置
        logger.info("加载配置文件...")
        config = Config("config.yaml")
        
        # 初始化组件
        logger.info("初始化组件...")
        browser = BrowserManager(headless=config.get('login.headless', False))
        browser.start()
        
        ocr = CaptchaRecognizer()
        login_manager = LoginManager(browser, ocr)
        query_manager = QueryManager(browser)
        booking_manager = BookingManager(browser, query_manager)
        
        # 初始化邮件通知器（如果配置了）
        email_notifier = None
        if config.get('email.sender_email'):
            email_notifier = EmailNotifier(
                smtp_server=config.get('email.smtp_server'),
                smtp_port=config.get('email.smtp_port'),
                sender_email=config.get('email.sender_email'),
                sender_password=config.get('email.sender_password'),
                use_ssl=config.get('email.use_ssl', True)
            )
        
        # 执行登录
        logger.info("开始登录...")
        login_success = login_manager.login(
            url=config.get('login.url'),
            username=config.get('user.username'),
            password=config.get('user.password'),
            auto_captcha=config.get('login.auto_captcha', True),
            max_retry=config.get('login.max_retry', 3)
        )
        
        if not login_success:
            logger.error("登录失败")
            if email_notifier:
                email_notifier.send_booking_failed(
                    config.get('user.email'),
                    "登录失败，请检查用户名和密码"
                )
            return
        
        # 导航到羽毛球场馆
        venue_type = config.get('booking.venue_type', '羽毛球')
        logger.info(f"正在进入{venue_type}场馆...")
        if not login_manager.navigate_to_venue(venue_type):
            logger.error("进入场馆失败")
            return
        
        logger.info("成功进入场馆页面，浏览器将保持打开...")
        # 这里可以添加预订逻辑
        # booking_success = booking_manager.auto_book(...)
        # if booking_success and email_notifier:
        #     email_notifier.send_booking_success(...)
        
    except Exception as e:
        logger.error(f"程序执行出错: {e}", exc_info=True)
    finally:
        if 'browser' in locals():
            browser.close()

def test_mode():
    """测试模式"""
    try:
        logger.info("========== 测试模式 ==========")
        # 加载配置
        logger.info("加载配置文件...")
        config = Config("config.yaml")
        # 硬编码测试配置
        browser = BrowserManager(headless=False)
        browser.start()
        query_manager = QueryManager(browser)
        booking_manager = BookingManager(browser, query_manager)
        ocr = CaptchaRecognizer()
        login_manager = LoginManager(browser, ocr)
        
        # 执行登录
        login_success = login_manager.login(
            url="https://ggtypt.cumtb.edu.cn/venue/login",
            username="17396154630",
            password="hhhSir012345",
            auto_captcha=True,
            max_retry=3
        )
        
        if login_success:
            logger.info("登录成功！")
        else:
            logger.error("登录失败")
            input("按回车键关闭浏览器...")
        # 导航到羽毛球场馆
        venue_type = config.get('booking.venue_type', '羽毛球')
        logger.info(f"正在进入{venue_type}场馆...")
        if not login_manager.navigate_to_venue(venue_type):
            logger.error("进入场馆失败")
            return
        
        logger.info("成功进入场馆页面，浏览器将保持打开...")
        time.sleep(10)
        logger.info("正在查询可用场地...")
        available_courts = query_manager.query_available_courts('2025-12-06')
        
        # 输出查询结果
        print("\n" + "="*60)
        print("可用场地查询结果:")
        print("="*60)
        for court_name, time_slots in available_courts.items():
            if time_slots:
                res = booking_manager.book_court(court_name, time_slots, ["王葭泐"])  # 尝试预订第一个可用时
                print(f"预订结果: {'成功' if res else '失败'}")
                print(f"\n{court_name}:")
                for slot in time_slots:
                    print(f"  - {slot}")
        print("="*60 + "\n")
        
        input("按回车键关闭浏览器...")
    except Exception as e:
        logger.error(f"测试失败: {e}", exc_info=True)
    finally:
        if 'browser' in locals():
            browser.close()

if __name__ == "__main__":
    import argparse    
    parser = argparse.ArgumentParser(description='羽毛球场地自动预订系统')
    parser.add_argument('--test', action='store_true', help='运行测试模式')
    args = parser.parse_args()
    
    if args.test:
        test_mode()
    else:
        main()