"""
周期性扫描流程管道
实现定时扫描场地可用性，并根据配置自动预订
"""
import traceback
from .base_pipeline import BasePipeline
from datetime import datetime, timedelta
import time
from typing import Optional, List, Tuple, Dict


class PeriodicScanningPipeline(BasePipeline):
    """
    周期性扫描流程管道
    
    功能：
    1. 定期扫描指定日期的场地可用性
    2. 检查是否有符合配置的时间段
    3. 自动预订（可配置）
    4. 发送邮件通知
    """
    
    def __init__(self):
        """初始化周期性扫描流程"""
        super().__init__()
        
        # 加载配置
        self.scan_interval = self.config.get('polling.polling_interval', 300)
        self.polling_time_slots = self.config.get('polling.polling_time_slots', [])
        self.polling_date = self.config.get('polling.polling_date', 
                                           datetime.now().strftime("%Y-%m-%d"))
        self.is_auto_booking = self.config.get('polling.is_auto_booking', False)
        self.partner_name = self.config.get('user.partner_name', '')
        self.venue_type = self.config.get('booking.venue_type', '羽毛球')
        
        # 运行状态
        self.is_running = False
        self.is_send_error_email = False
        
        self.logger.info("=" * 60)
        self.logger.info("周期性扫描流程初始化")
        self.logger.info(f"  扫描日期: {self.polling_date}")
        self.logger.info(f"  扫描间隔: {self.scan_interval}秒")
        self.logger.info(f"  目标时间段: {self.polling_time_slots}")
        self.logger.info(f"  自动预订: {'是' if self.is_auto_booking else '否'}")
        self.logger.info("=" * 60)
    
    def run(self):
        """执行周期性扫描流程"""
        # 步骤0: 参数验证
        if not self._validate_config():
            return
        
        # 步骤1: 执行登录，确保登录状态有效
        if not self._initialize_session():
            return
        
        # 步骤2: 进入扫描循环
        self._run_scan_loop()
    
    def _validate_config(self) -> bool:
        """
        验证配置参数
        
        Returns:
            配置是否有效
        """
        if not self.polling_time_slots:
            self.logger.error("❌ 轮询时间段未配置")
            self.logger.error("   请在 config.yaml 中设置 polling.polling_time_slots")
            
            if not self.is_send_error_email:
                self._send_config_error_email()
                self.is_send_error_email = True
            
            return False
        
        if not self.polling_date:
            self.logger.error("❌ 轮询日期未配置")
            return False
        
        return True
    
    def _send_config_error_email(self):
        """发送配置错误通知邮件"""
        if self.email_notifier:
            try:
                self.email_notifier.send_email(
                    to_email=self.config.get('user.email'),
                    subject="⚠️ 羽毛球预订系统配置错误",
                    content="""
                    <h2>配置错误通知</h2>
                    <p>系统检测到配置参数缺失或错误。</p>
                    <h3>问题：</h3>
                    <ul>
                        <li>轮询时间段 (polling.polling_time_slots) 未配置</li>
                    </ul>
                    <h3>解决方案：</h3>
                    <p>请在 <code>config.yaml</code> 中添加以下配置：</p>
                    <pre>
polling:
  polling_time_slots:
    - "18:00-19:00"
    - "19:00-20:00"
  polling_date: "2025-12-15"
  polling_interval: 300
  is_auto_booking: true
                    </pre>
                    """,
                    html=True
                )
                self.logger.info("配置错误通知邮件已发送")
            except Exception as e:
                self.logger.error(f"发送配置错误邮件失败: {e}")
    
    def _initialize_session(self) -> bool:
        """
        初始化会话（登录）
        
        Returns:
            是否初始化成功
        """
        try:
            self.logger.info("正在初始化会话...")
            self.start_pipeline()
            self.logger.info("✓ 会话初始化成功")
            return True
        except Exception as e:
            self.logger.error(f"❌ 会话初始化失败: {e}", exc_info=True)
            return False
    
    def _run_scan_loop(self):
        """运行扫描循环"""
        self.is_running = True
        scan_count = 0
        
        self.logger.info("🚀 周期性扫描流程启动")
        self.logger.info(f"   目标日期: {self.polling_date}")
        self.logger.info(f"   扫描间隔: {self.scan_interval}秒")
        
        while self.is_running:
            try:
                scan_count += 1
                self.logger.info("")
                self.logger.info("=" * 60)
                self.logger.info(f"开始第 {scan_count} 轮扫描")
                self.logger.info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                self.logger.info("=" * 60)
                
                start_time = time.time()
                
                # 执行扫描
                self._perform_scan()
                
                elapsed_time = time.time() - start_time
                self.logger.info("-" * 60)
                self.logger.info(f"✓ 第 {scan_count} 轮扫描完成 (耗时: {elapsed_time:.2f}秒)")
                self.logger.info("-" * 60)
                
                # 等待下一次扫描
                if self.is_running:
                    self._wait_for_next_scan()
                    
            except KeyboardInterrupt:
                self.logger.info("收到中断信号，正在停止...")
                self.stop()
                break
            except Exception as e:
                self.logger.error(f"❌ 扫描过程中发生错误: {e}", exc_info=True)
                self.logger.info("等待60秒后继续...")
                time.sleep(60)
    
    def _wait_for_next_scan(self):
        """等待下一次扫描"""
        next_scan_time = datetime.now() + timedelta(seconds=self.scan_interval)
        self.logger.info(f"⏰ 下次扫描时间: {next_scan_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(f"   等待 {self.scan_interval} 秒...\n")
        time.sleep(self.scan_interval)
    
    def _perform_scan(self):
        """
        执行单次扫描
        
        流程：
        1. 查询可用场地
        2. 筛选符合条件的场地
        3. 自动预订（如果启用）
        4. 发送通知邮件
        """
        # 1. 查询可用场地
        available_courts = self._query_available_courts()
        
        if not available_courts:
            self.logger.info("未找到任何可用场地")
            return
        # 打印可用场地信息
        self.logger.info(f"✓ 找到 {len(available_courts)} 个可用场地:")
        for court_name, time_slots in available_courts.items():
            if time_slots:
                self.logger.info(f"  • {court_name}: {time_slots}")
            
        # 2. 筛选符合条件的场地
        valid_courts = self._filter_valid_courts(available_courts, self.polling_time_slots)
        
        if not valid_courts:
            self.logger.info("未找到符合条件的场地")
            return
        
        # 3. 处理预订
        booked_courts = self._process_bookings(valid_courts, self.polling_time_slots, True, self.partner_name)
        
        # 4. 发送通知
        self._send_notifications(booked_courts, valid_courts)
    
    def _query_available_courts(self) -> Dict[str, List[str]]:
        """
        查询可用场地
        
        Returns:
            场地可用性字典 {场地名: [时间段列表]}
        """
        self.logger.info(f"🔍 查询 {self.polling_date} 的可用场地...")
        
        try:
            available_courts = self.query_manager.query_available_courts(
                self.polling_date
            )
            
            self.logger.info(f"查询完成，共找到 {len(available_courts)} 个场地")
            
            # 详细输出查询结果
            for court_name, time_slots in available_courts.items():
                if time_slots:
                    self.logger.info(f"  • {court_name}: {', '.join(time_slots)}")
            
            return available_courts
            
        except Exception as e:
            self.logger.error(f"查询失败: {e}", exc_info=True)
            return {}

    
    def _send_notifications(self, 
                           booked_courts: List[Tuple[str, List[str]]],
                           valid_courts: List[Tuple[str, List[str]]]):
        """
        发送通知邮件
        
        Args:
            booked_courts: 已预订的场地
            valid_courts: 符合条件的场地（未预订）
        """
        if not self.email_notifier:
            self.logger.warning("邮件通知未配置，跳过邮件发送")
            return
        
        user_email = self.config.get('user.email')
        if not user_email:
            self.logger.warning("用户邮箱未配置，跳过邮件发送")
            return
        
        # 发送预订成功通知
        if booked_courts:
            self._send_booking_success_emails(booked_courts, user_email)
        
        # 发送可用场地通知（仅当未预订时）
        elif valid_courts:
            self._send_availability_email(valid_courts, user_email)
    
    def _send_booking_success_emails(self, 
                                     booked_courts: List[Tuple[str, List[str]]],
                                     user_email: str):
        """发送预订成功通知邮件"""
        for court_name, time_slots in booked_courts:
            self.logger.info(f"📧 发送预订成功通知: {court_name}")
            
            try:
                self.email_notifier.send_booking_success(
                    to_email=user_email,
                    booking_info={
                        'venue_type': self.venue_type,
                        'date': self.polling_date,
                        'time_slot': ', '.join(time_slots),
                        'court_name': court_name,
                        'partners': [self.partner_name] if self.partner_name else []
                    }
                )
                self.logger.info("  ✓ 邮件发送成功")
            except Exception as e:
                self.logger.error(f"  ❌ 邮件发送失败: {e}")
    
    def _send_availability_email(self, 
                                valid_courts: List[Tuple[str, List[str]]],
                                user_email: str):
        """发送可用场地通知邮件"""
        self.logger.info("📧 发送可用场地通知")
        
        try:
            availability_info = {
                court_name: time_slots 
                for court_name, time_slots in valid_courts
            }
            
            self.email_notifier.send_availability_notification(
                to_email=user_email,
                available_courts=availability_info
            )
            self.logger.info("  ✓ 邮件发送成功")
        except Exception as e:
            self.logger.error(f"  ❌ 邮件发送失败: {e}")
    
    def stop(self):
        """停止周期性扫描"""
        self.logger.info("🛑 正在停止周期性扫描流程...")
        self.is_running = False
        self.logger.info("✓ 周期性扫描流程已停止")