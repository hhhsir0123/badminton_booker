import traceback
from .base_pipeline import BasePipeline
from datetime import datetime, timedelta
import time
from typing import Optional, List, Tuple, Dict


"""
根据配置，完成定时抢票的pipeline,包括登录、抢票和通知等功能
"""



class TicketGrabbingPipeline(BasePipeline):
    """自动抢票流程管理类"""
    
    def __init__(self):
        """初始化抢票流程"""
        super().__init__()
        
        # 加载配置
        self.venue_type = self.config.get('booking.venue_type', '羽毛球')
        self.preferred_time_slots = self.config.get('booking.preferred_time_slots', [])
        self.booking_days_ahead = self.config.get('booking.booking_days_ahead', 2)  # 提前几天预订
        self.advance_time = self.config.get('booking.advance_time', 5)  # 提前多少秒开始抢票
        self.target_time_in_day = self.config.get('booking.target_time_in_day', "07:00:00") # 目标抢票时间点，格式 'HH:MM:SS'
        self.booking_date = self.config.get('booking.booking_date', None)  # 绝对抢票日期，格式 'YYYY-MM-DD'
        self.partner_name = self.config.get('user.partner_name', '')
        self.target_time = self.__init_target_time()
        self.booking_date_str = self.target_time.strftime("%Y-%m-%d")
        if self.target_time:
            self.pre_one_day_booking_date_str = (self.target_time - timedelta(days=1)).strftime("%Y-%m-%d")
            self.pre_two_day_booking_date_str = (self.target_time - timedelta(days=2)).strftime("%Y-%m-%d")
        else:
            raise

    def __init_target_time(self):
        """计算抢票的绝对时间点"""
        try:
            target_time: Optional[datetime] = None
            if self.booking_date:
                # 使用绝对抢票日期
                target_datetime_str = f"{self.booking_date} {self.target_time_in_day}"
                target_time = datetime.strptime(target_datetime_str, "%Y-%m-%d %H:%M:%S")
            else:
                target_hour, target_minute, target_second = map(int, self.target_time_in_day.split(':'))
                
                # 计算目标日期（提前booking_days_ahead天）
                target_date = datetime.now() + timedelta(days=self.booking_days_ahead)
                
                # 构建完整的目标时间点
                self.target_time = target_date.replace(
                    hour=target_hour,
                    minute=target_minute,
                    second=target_second,
                    microsecond=0
                )
            self.logger.info(f"目标抢票时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            return target_time
        except ValueError as e:
            self.logger.error(f"目标时间格式错误: {target_time}，应为 'HH:MM:SS' 格式")
            return None
        
    def wait_until_target_time(self):
        """等待到目标时间"""
        if not self.target_time:
            return
        
        # 计算需要提前的时间点
        start_time = self.target_time - timedelta(seconds=self.advance_time)
        
        while datetime.now() < start_time:
            remaining = (start_time - datetime.now()).total_seconds()
            if remaining > 60:
                self.logger.info(f"距离开始抢票还有 {remaining/60:.1f} 分钟")
                time.sleep(30)
            elif remaining > 0:
                self.logger.info(f"距离开始抢票还有 {remaining:.0f} 秒")
                time.sleep(1)
            else:
                break
        
        self.logger.info("准备开始抢票...")
        
    def run(self):
        """执行抢票流程"""
        try:
            # 开始启动
            self.logger.info("正在初始化会话...")
            self.start_pipeline()
            self.booking_manager.click_partner(self.partner_name)
            self.logger.info("✓ 会话初始化成功")
            
            # 等待到目标时间
            # self.wait_until_target_time()

            # 执行抢票逻辑
            success = self.grab_tickets()
            
            if success:
                self.logger.info("抢票成功!")
            else:
                self.logger.warning("抢票失败")
            return success
            
        except Exception as e:
            error_msg = f"抢票过程出现异常: {str(e)}\n{traceback.format_exc()}"
            self.logger.error(error_msg)
            return False
        finally:
            self.cleanup()
            
    def grab_tickets(self) -> bool:
        """执行抢票操作"""
        try:
            retry_cnt = 0
            while True:
                self.__refresh_booking_info()
                if self.query_manager.is_date_exist(self.booking_date_str):
                    available_courts = self.query_manager.query_available_courts(self.booking_date_str)
                    valid_courts = self._filter_valid_courts(available_courts, self.preferred_time_slots)
                    if not valid_courts:
                        retry_cnt += 1
                        self.logger.info(f"日期 {self.booking_date_str} 无可用场地，继续刷新...")
                        if retry_cnt >= 3:
                            return False
                    booked_courts = self._process_bookings(valid_courts, self.preferred_time_slots)
                    self.logger.info(f"已成功预订的场地: {booked_courts}")
                else:
                    time.sleep(1)
        except Exception as e:
            self.logger.error(f"抢票过程中出现异常: {str(e)}")
            return False

    def __refresh_booking_info(self):
        """刷新预订信息"""
        self.logger.info("正在刷新预订信息...")
        self.browser.driver.refresh()
        self.query_manager.click_close_button()
        time.sleep(1)  # 等待页面加载完成