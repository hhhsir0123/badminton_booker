# test_email.py - 邮件功能测试脚本
"""
测试邮件发送功能
使用方法: python test_email.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.email_notifier import EmailNotifier, test_email_notifier
from src.config import Config

def main():
    print("=" * 60)
    print("羽毛球预订系统 - 邮件功能测试")
    print("=" * 60)
    
    # 方式1: 使用内置测试函数
    print("\n[方式1] 使用配置文件测试")
    test_email_notifier()
    
    # 方式2: 手动测试各种邮件类型
    print("\n[方式2] 测试各种邮件类型")
    
    try:
        config = Config("config.yaml")
        
        notifier = EmailNotifier(
            smtp_server=config.get('email.smtp_server'),
            smtp_port=config.get('email.smtp_port'),
            sender_email=config.get('email.sender_email'),
            sender_password=config.get('email.sender_password'),
            use_ssl=config.get('email.use_ssl', True)
        )
        
        user_email = config.get('user.email')
        
        # 测试1: 预订成功通知
        print("\n1. 测试预订成功通知...")
        notifier.send_booking_success(user_email, {
            'venue_type': '羽毛球',
            'date': '2025-12-06',
            'time_slot': '18:00-19:00',
            'court_name': '1号场地',
            'partners': ['张三', '李四']
        })
        
        # 测试2: 预订失败通知
        print("2. 测试预订失败通知...")
        notifier.send_booking_failed(user_email, "场地已满", {
            'attempted_date': '2025-12-06',
            'attempted_time': '18:00-19:00',
            'retry_count': 5
        })
        
        # 测试3: 场地可用性通知
        print("3. 测试场地可用性通知...")
        notifier.send_availability_notification(user_email, {
            '1号场地': ['18:00-19:00', '19:00-20:00'],
            '2号场地': ['20:00-21:00'],
        })
        
        print("\n✅ 所有测试邮件已发送，请检查收件箱")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()