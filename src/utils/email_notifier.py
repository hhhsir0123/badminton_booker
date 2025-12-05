"""
邮件通知模块 - 完善版
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime
from typing import Optional, Dict, List
from .logger import setup_logger

logger = setup_logger(__name__)


class EmailNotifier:
    """邮件通知器"""
    
    def __init__(self, smtp_server: str, smtp_port: int, 
                 sender_email: str, sender_password: str, use_ssl: bool = True):
        """
        初始化邮件通知器
        
        Args:
            smtp_server: SMTP服务器地址 (如 smtp.qq.com)
            smtp_port: SMTP端口 (SSL通常是465, TLS通常是587)
            sender_email: 发件人邮箱
            sender_password: 发件人密码（授权码，不是登录密码）
            use_ssl: 是否使用SSL
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.use_ssl = use_ssl
        
        logger.info(f"邮件通知器初始化: {smtp_server}:{smtp_port} ({sender_email})")
    
    def send_email(self, to_email: str, subject: str, content: str, 
                   html: bool = False) -> bool:
        """
        发送邮件
        
        Args:
            to_email: 收件人邮箱
            subject: 邮件主题
            content: 邮件内容
            html: 是否为HTML格式
            
        Returns:
            是否发送成功
        """
        try:
            # 创建邮件
            msg = MIMEMultipart()
            
            # 修复From字段 - 使用Header编码中文
            sender_name = Header('羽毛球预订系统', 'utf-8').encode()
            msg['From'] = f"{sender_name} <{self.sender_email}>"
            
            msg['To'] = to_email
            msg['Subject'] = Header(subject, 'utf-8')
            
            # 添加邮件内容
            msg.attach(MIMEText(content, 'html' if html else 'plain', 'utf-8'))
            
            # 连接SMTP服务器并发送
            if self.use_ssl:
                logger.debug(f"使用SSL连接 {self.smtp_server}:{self.smtp_port}")
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
            else:
                logger.debug(f"使用TLS连接 {self.smtp_server}:{self.smtp_port}")
                server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30)
                server.starttls()
            
            server.login(self.sender_email, self.sender_password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"✓ 邮件发送成功: {to_email} - {subject}")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP认证失败: {e}")
            logger.error("请检查邮箱地址和授权码是否正确")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP错误: {e}")
            return False
        except Exception as e:
            logger.error(f"邮件发送失败: {e}", exc_info=True)
            return False
    
    def send_booking_success(self, to_email: str, booking_info: Dict) -> bool:
        """
        发送预订成功通知
        
        Args:
            to_email: 收件人邮箱
            booking_info: 预订信息字典
                - venue_type: 场地类型
                - date: 预订日期
                - time_slot: 时间段
                - court_name: 场地名称
                - partners: 同伴列表
                
        Returns:
            是否发送成功
        """
        subject = "✅ 羽毛球场地预订成功"
        
        # 格式化同伴信息
        partners_str = "、".join(booking_info.get('partners', [])) if booking_info.get('partners') else "无"
        
        content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                          color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .info-item {{ background: white; margin: 10px 0; padding: 15px; border-left: 4px solid #667eea; }}
                .label {{ font-weight: bold; color: #667eea; }}
                .footer {{ margin-top: 20px; text-align: center; color: #999; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🎉 预订成功！</h1>
                </div>
                <div class="content">
                    <p>您好，您的羽毛球场地预订已成功！</p>
                    
                    <div class="info-item">
                        <span class="label">📍 场地类型：</span>{booking_info.get('venue_type', 'N/A')}
                    </div>
                    
                    <div class="info-item">
                        <span class="label">📅 预订日期：</span>{booking_info.get('date', 'N/A')}
                    </div>
                    
                    <div class="info-item">
                        <span class="label">⏰ 时间段：</span>{booking_info.get('time_slot', 'N/A')}
                    </div>
                    
                    <div class="info-item">
                        <span class="label">🏸 场地编号：</span>{booking_info.get('court_name', 'N/A')}
                    </div>
                    
                    <div class="info-item">
                        <span class="label">👥 同伴：</span>{partners_str}
                    </div>
                    
                    <p style="margin-top: 20px; padding: 15px; background: #fff3cd; border-left: 4px solid #ffc107;">
                        ⚠️ <strong>温馨提示：</strong>请提前10分钟到场，带好运动装备。
                    </p>
                </div>
                <div class="footer">
                    <p>本邮件由羽毛球自动预订系统发送</p>
                    <p>发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, content, html=True)
    
    def send_booking_failed(self, to_email: str, reason: str, details: Dict = None) -> bool:
        """
        发送预订失败通知
        
        Args:
            to_email: 收件人邮箱
            reason: 失败原因
            details: 详细信息（可选）
                - attempted_date: 尝试预订的日期
                - attempted_time: 尝试预订的时间
                - retry_count: 重试次数
            
        Returns:
            是否发送成功
        """
        subject = "❌ 羽毛球场地预订失败"
        
        details_html = ""
        if details:
            details_html = f"""
            <div class="info-item">
                <span class="label">📅 尝试日期：</span>{details.get('attempted_date', 'N/A')}
            </div>
            <div class="info-item">
                <span class="label">⏰ 尝试时间：</span>{details.get('attempted_time', 'N/A')}
            </div>
            <div class="info-item">
                <span class="label">🔄 重试次数：</span>{details.get('retry_count', 'N/A')}
            </div>
            """
        
        content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); 
                          color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .info-item {{ background: white; margin: 10px 0; padding: 15px; border-left: 4px solid #f5576c; }}
                .label {{ font-weight: bold; color: #f5576c; }}
                .footer {{ margin-top: 20px; text-align: center; color: #999; font-size: 12px; }}
                .reason {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; margin: 15px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>😔 预订失败</h1>
                </div>
                <div class="content">
                    <p>很抱歉，场地预订未能成功。</p>
                    
                    <div class="reason">
                        <strong>失败原因：</strong>{reason}
                    </div>
                    
                    {details_html}
                    
                    <p style="margin-top: 20px; padding: 15px; background: #d1ecf1; border-left: 4px solid #0c5460;">
                        💡 <strong>建议：</strong><br>
                        • 请检查网络连接是否正常<br>
                        • 确认账号密码是否正确<br>
                        • 可以尝试手动登录预订<br>
                        • 联系管理员获取帮助
                    </p>
                </div>
                <div class="footer">
                    <p>本邮件由羽毛球自动预订系统发送</p>
                    <p>发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, content, html=True)
    
    def send_availability_notification(self, to_email: str, available_courts: Dict) -> bool:
        """
        发送场地可用性通知
        
        Args:
            to_email: 收件人邮箱
            available_courts: 可用场地字典 {场地名: [时间段列表]}
            
        Returns:
            是否发送成功
        """
        subject = "📢 羽毛球场地可用性通知"
        
        # 构建可用场地列表HTML
        courts_html = ""
        total_slots = 0
        for court_name, time_slots in available_courts.items():
            if time_slots:
                total_slots += len(time_slots)
                times_str = "、".join(time_slots)
                courts_html += f"""
                <div class="info-item">
                    <span class="label">{court_name}：</span>{times_str}
                </div>
                """
        
        if not courts_html:
            courts_html = "<p>暂无可用场地</p>"
        
        content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, "Microsoft YaHei", sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%); 
                          color: #333; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .info-item {{ background: white; margin: 10px 0; padding: 15px; border-left: 4px solid #a8edea; }}
                .label {{ font-weight: bold; color: #00bcd4; }}
                .footer {{ margin-top: 20px; text-align: center; color: #999; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🏸 场地可用性报告</h1>
                    <p>共发现 {total_slots} 个可用时段</p>
                </div>
                <div class="content">
                    <p>以下是当前可用的场地和时间：</p>
                    {courts_html}
                    <p style="margin-top: 20px; text-align: center;">
                        <strong>请及时预订心仪的场地！</strong>
                    </p>
                </div>
                <div class="footer">
                    <p>本邮件由羽毛球自动预订系统发送</p>
                    <p>发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, content, html=True)
    
    def send_test_email(self, to_email: str) -> bool:
        """
        发送测试邮件
        
        Args:
            to_email: 收件人邮箱
            
        Returns:
            是否发送成功
        """
        subject = "✅ 邮件配置测试"
        content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, "Microsoft YaHei", sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 40px; text-align: center; }}
                .success {{ color: #28a745; font-size: 48px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="success">✅</div>
                <h2>邮件配置成功！</h2>
                <p>如果您收到这封邮件，说明邮件通知功能已正常工作。</p>
                <hr>
                <p style="color: #999; font-size: 12px;">
                    发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                    发件人: {self.sender_email}<br>
                    服务器: {self.smtp_server}:{self.smtp_port}
                </p>
            </div>
        </body>
        </html>
        """
        
        return self.send_email(to_email, subject, content, html=True)


def test_email_notifier():
    """测试邮件功能"""
    import yaml
    
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    email_config = config.get('email', {})
    user_email = config.get('user', {}).get('email')
    
    if not all([email_config.get('smtp_server'), email_config.get('sender_email'), 
                email_config.get('sender_password'), user_email]):
        print("❌ 邮件配置不完整，请检查config.yaml")
        return False
    
    # 初始化邮件通知器
    notifier = EmailNotifier(
        smtp_server=email_config['smtp_server'],
        smtp_port=email_config['smtp_port'],
        sender_email=email_config['sender_email'],
        sender_password=email_config['sender_password'],
        use_ssl=email_config.get('use_ssl', True)
    )
    
    # 发送测试邮件
    print(f"正在向 {user_email} 发送测试邮件...")
    success = notifier.send_test_email(user_email)
    
    if success:
        print(f"✅ 测试邮件发送成功！请检查 {user_email} 的收件箱")
    else:
        print("❌ 测试邮件发送失败，请检查配置")
    
    return success