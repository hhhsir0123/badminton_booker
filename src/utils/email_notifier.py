"""
邮件通知模块
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from .logger import setup_logger

logger = setup_logger(__name__)

class EmailNotifier:
    """邮件通知器"""
    
    def __init__(self, smtp_server: str, smtp_port: int, 
                 sender_email: str, sender_password: str, use_ssl: bool = True):
        """
        初始化邮件通知器
        
        Args:
            smtp_server: SMTP服务器地址
            smtp_port: SMTP端口
            sender_email: 发件人邮箱
            sender_password: 发件人密码（授权码）
            use_ssl: 是否使用SSL
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.use_ssl = use_ssl
    
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
            msg['From'] = self.sender_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            # 添加邮件内容
            msg.attach(MIMEText(content, 'html' if html else 'plain', 'utf-8'))
            
            # 发送邮件
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            else:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
            
            server.login(self.sender_email, self.sender_password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"邮件发送成功: {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"邮件发送失败: {e}", exc_info=True)
            return False
    
    def send_booking_success(self, to_email: str, booking_info: dict) -> bool:
        """
        发送预订成功通知
        
        Args:
            to_email: 收件人邮箱
            booking_info: 预订信息
            
        Returns:
            是否发送成功
        """
        subject = "羽毛球场地预订成功通知"
        content = f"""
        <h2>预订成功！</h2>
        <p>您的场地预订已成功完成。</p>
        <ul>
            <li>场地类型: {booking_info.get('venue_type', 'N/A')}</li>
            <li>日期: {booking_info.get('date', 'N/A')}</li>
            <li>时间: {booking_info.get('time_slot', 'N/A')}</li>
            <li>场地编号: {booking_info.get('court_number', 'N/A')}</li>
        </ul>
        <p>请准时到场！</p>
        """
        return self.send_email(to_email, subject, content, html=True)
    
    def send_booking_failed(self, to_email: str, reason: str) -> bool:
        """
        发送预订失败通知
        
        Args:
            to_email: 收件人邮箱
            reason: 失败原因
            
        Returns:
            是否发送成功
        """
        subject = "羽毛球场地预订失败通知"
        content = f"""
        <h2>预订失败</h2>
        <p>很抱歉，场地预订失败。</p>
        <p>失败原因: {reason}</p>
        <p>请稍后重试或手动预订。</p>
        """
        return self.send_email(to_email, subject, content, html=True)