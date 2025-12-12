"""
周期性扫描流程管道的测试代码
测试文件：tests/test_periodic_scanning_pipeline.py
"""
import unittest
from unittest.mock import Mock, MagicMock, patch, call
from datetime import datetime, timedelta
import time
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline


class TestPeriodicScanningPipeline(unittest.TestCase):
    """周期性扫描流程管道的单元测试"""
    
    def setUp(self):
        """每个测试前的设置"""
        # Mock配置
        self.mock_config = {
            'polling': {
                'polling_interval': 10,
                'polling_time_slots': ['18:00-19:00', '19:00-20:00'],
                'polling_date': '2025-12-15',
                'is_auto_booking': True
            },
            'booking': {
                'partner_name': '测试伙伴',
                'venue_type': '羽毛球'
            },
            'user': {
                'email': 'hhhsir0123@gmail.com'
            }
        }
        
        # 创建pipeline实例并mock其依赖
        with patch.object(PeriodicScanningPipeline, '__init__', lambda x: None):
            self.pipeline = PeriodicScanningPipeline()
            self._setup_mocks()
            # 手动调用初始化逻辑（但使用mock的config）
            self._initialize_pipeline()
    
    def _setup_mocks(self):
        """设置Mock对象"""
        # Mock config
        self.pipeline.config = Mock()
        self.pipeline.config.get = Mock(side_effect=self._mock_config_get)
        
        # Mock logger
        self.pipeline.logger = Mock()
        
        # Mock managers
        self.pipeline.query_manager = Mock()
        self.pipeline.booking_manager = Mock()
        self.pipeline.email_notifier = Mock()
    
    def _initialize_pipeline(self):
        """手动初始化pipeline属性"""
        # 模拟__init__中的逻辑
        self.pipeline.scan_interval = self.pipeline.config.get('polling.polling_interval', 300)
        self.pipeline.polling_time_slots = self.pipeline.config.get('polling.polling_time_slots', [])
        self.pipeline.polling_date = self.pipeline.config.get('polling.polling_date', 
                                           datetime.now().strftime("%Y-%m-%d"))
        self.pipeline.is_auto_booking = self.pipeline.config.get('polling.is_auto_booking', False)
        self.pipeline.partner_name = self.pipeline.config.get('booking.partner_name', '')
        self.pipeline.venue_type = self.pipeline.config.get('booking.venue_type', '羽毛球')
        self.pipeline.is_running = False
        self.pipeline.is_send_error_email = False
    
    def _mock_config_get(self, key, default=None):
        """Mock配置获取"""
        keys = key.split('.')
        value = self.mock_config
        for k in keys:
            value = value.get(k, {})
        return value if value != {} else default
    
    def tearDown(self):
        """每个测试后的清理"""
        if hasattr(self.pipeline, 'is_running'):
            self.pipeline.is_running = False
    
    # ==================== 配置验证测试 ====================
    
    def test_validate_config_success(self):
        """测试配置验证 - 成功"""
        result = self.pipeline._validate_config()
        self.assertTrue(result)
        self.pipeline.logger.error.assert_not_called()
    
    def test_validate_config_missing_time_slots(self):
        """测试配置验证 - 缺少时间段"""
        self.pipeline.polling_time_slots = []
        result = self.pipeline._validate_config()
        self.assertFalse(result)
        self.pipeline.logger.error.assert_called()
    
    def test_validate_config_missing_date(self):
        """测试配置验证 - 缺少日期"""
        self.pipeline.polling_date = None
        result = self.pipeline._validate_config()
        self.assertFalse(result)
        self.pipeline.logger.error.assert_called()
    
    def test_send_config_error_email(self):
        """测试发送配置错误邮件"""
        self.pipeline._send_config_error_email()
        self.pipeline.email_notifier.send_email.assert_called_once()
        call_args = self.pipeline.email_notifier.send_email.call_args
        self.assertEqual(call_args[1]['to_email'], 'test@example.com')
        self.assertIn('配置错误', call_args[1]['subject'])
    
    # ==================== 会话初始化测试 ====================
    
    def test_initialize_session_success(self):
        """测试会话初始化 - 成功"""
        self.pipeline.start_pipeline = Mock()
        result = self.pipeline._initialize_session()
        self.assertTrue(result)
        self.pipeline.start_pipeline.assert_called_once()
    
    def test_initialize_session_failure(self):
        """测试会话初始化 - 失败"""
        self.pipeline.start_pipeline = Mock(side_effect=Exception("登录失败"))
        result = self.pipeline._initialize_session()
        self.assertFalse(result)
        self.pipeline.logger.error.assert_called()
    
    # ==================== 查询测试 ====================
    
    def test_query_available_courts_success(self):
        """测试查询可用场地 - 成功"""
        expected_courts = {
            '1号场地': ['18:00-19:00', '19:00-20:00', '20:00-21:00'],
            '2号场地': ['18:00-19:00']
        }
        self.pipeline.query_manager.query_available_courts.return_value = expected_courts
        
        result = self.pipeline._query_available_courts()
        
        self.assertEqual(result, expected_courts)
        self.pipeline.query_manager.query_available_courts.assert_called_once_with('2025-12-15')
    
    def test_query_available_courts_failure(self):
        """测试查询可用场地 - 失败"""
        self.pipeline.query_manager.query_available_courts.side_effect = Exception("查询失败")
        
        result = self.pipeline._query_available_courts()
        
        self.assertEqual(result, {})
        self.pipeline.logger.error.assert_called()
    
    # ==================== 筛选测试 ====================
    
    def test_filter_valid_courts_all_match(self):
        """测试筛选场地 - 全部匹配"""
        available_courts = {
            '1号场地': ['18:00-19:00', '19:00-20:00', '20:00-21:00'],
            '2号场地': ['18:00-19:00', '19:00-20:00']
        }
        
        result = self.pipeline._filter_valid_courts(available_courts)
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0][0], '1号场地')
        self.assertEqual(result[1][0], '2号场地')
    
    def test_filter_valid_courts_partial_match(self):
        """测试筛选场地 - 部分匹配"""
        available_courts = {
            '1号场地': ['18:00-19:00', '19:00-20:00'],
            '2号场地': ['18:00-19:00'],  # 缺少19:00-20:00
            '3号场地': ['20:00-21:00']   # 不匹配
        }
        
        result = self.pipeline._filter_valid_courts(available_courts)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], '1号场地')
    
    def test_filter_valid_courts_no_match(self):
        """测试筛选场地 - 无匹配"""
        available_courts = {
            '1号场地': ['20:00-21:00'],
            '2号场地': ['21:00-22:00']
        }
        
        result = self.pipeline._filter_valid_courts(available_courts)
        
        self.assertEqual(len(result), 0)
    
    def test_check_time_slots_available_true(self):
        """测试时间段检查 - 可用"""
        available_times = ['18:00-19:00', '19:00-20:00', '20:00-21:00']
        result = self.pipeline._check_time_slots_available(available_times)
        self.assertTrue(result)
    
    def test_check_time_slots_available_false(self):
        """测试时间段检查 - 不可用"""
        available_times = ['18:00-19:00', '20:00-21:00']  # 缺少19:00-20:00
        result = self.pipeline._check_time_slots_available(available_times)
        self.assertFalse(result)
    
    # ==================== 预订测试 ====================
    
    def test_process_bookings_auto_booking_disabled(self):
        """测试预订处理 - 自动预订未启用"""
        self.pipeline.is_auto_booking = False
        valid_courts = [('1号场地', ['18:00-19:00', '19:00-20:00'])]
        
        result = self.pipeline._process_bookings(valid_courts)
        
        self.assertEqual(result, [])
        self.pipeline.booking_manager.book_court.assert_not_called()
    
    def test_process_bookings_success(self):
        """测试预订处理 - 预订成功"""
        valid_courts = [
            ('1号场地', ['18:00-19:00', '19:00-20:00']),
            ('2号场地', ['18:00-19:00', '19:00-20:00'])
        ]
        self.pipeline.booking_manager.book_court.return_value = True
        
        result = self.pipeline._process_bookings(valid_courts)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], '1号场地')
        # 预订成功后应该停止，不再预订2号场地
        self.pipeline.booking_manager.book_court.assert_called_once()
    
    def test_process_bookings_failure(self):
        """测试预订处理 - 预订失败"""
        valid_courts = [('1号场地', ['18:00-19:00', '19:00-20:00'])]
        self.pipeline.booking_manager.book_court.return_value = False
        
        result = self.pipeline._process_bookings(valid_courts)
        
        self.assertEqual(len(result), 0)
        self.pipeline.logger.warning.assert_called()
    
    def test_process_bookings_exception(self):
        """测试预订处理 - 抛出异常"""
        valid_courts = [('1号场地', ['18:00-19:00', '19:00-20:00'])]
        self.pipeline.booking_manager.book_court.side_effect = Exception("预订异常")
        
        result = self.pipeline._process_bookings(valid_courts)
        
        self.assertEqual(len(result), 0)
        self.pipeline.logger.error.assert_called()
    
    # ==================== 通知测试 ====================
    
    def test_send_notifications_booked(self):
        """测试发送通知 - 已预订"""
        booked_courts = [('1号场地', ['18:00-19:00', '19:00-20:00'])]
        valid_courts = [('2号场地', ['18:00-19:00', '19:00-20:00'])]
        
        self.pipeline._send_notifications(booked_courts, valid_courts)
        
        # 应该发送预订成功邮件，不发送可用性邮件
        self.pipeline.email_notifier.send_booking_success.assert_called_once()
        self.pipeline.email_notifier.send_availability_notification.assert_not_called()
    
    def test_send_notifications_available_only(self):
        """测试发送通知 - 仅可用场地"""
        booked_courts = []
        valid_courts = [('1号场地', ['18:00-19:00', '19:00-20:00'])]
        
        self.pipeline._send_notifications(booked_courts, valid_courts)
        
        # 应该发送可用性邮件，不发送预订成功邮件
        self.pipeline.email_notifier.send_booking_success.assert_not_called()
        self.pipeline.email_notifier.send_availability_notification.assert_called_once()
    
    def test_send_notifications_no_email_notifier(self):
        """测试发送通知 - 邮件通知未配置"""
        self.pipeline.email_notifier = None
        booked_courts = [('1号场地', ['18:00-19:00'])]
        valid_courts = []
        
        self.pipeline._send_notifications(booked_courts, valid_courts)
        
        self.pipeline.logger.warning.assert_called()
    
    def test_send_booking_success_emails(self):
        """测试发送预订成功邮件"""
        booked_courts = [
            ('1号场地', ['18:00-19:00', '19:00-20:00']),
            ('2号场地', ['18:00-19:00'])
        ]
        
        self.pipeline._send_booking_success_emails(booked_courts, 'test@example.com')
        
        # 应该发送2封邮件
        self.assertEqual(self.pipeline.email_notifier.send_booking_success.call_count, 2)
    
    def test_send_availability_email(self):
        """测试发送可用性邮件"""
        valid_courts = [
            ('1号场地', ['18:00-19:00', '19:00-20:00']),
            ('2号场地', ['18:00-19:00'])
        ]
        
        self.pipeline._send_availability_email(valid_courts, 'test@example.com')
        
        self.pipeline.email_notifier.send_availability_notification.assert_called_once()
        call_args = self.pipeline.email_notifier.send_availability_notification.call_args
        self.assertEqual(len(call_args[1]['available_courts']), 2)
    
    # ==================== 完整流程测试 ====================
    
    def test_perform_scan_full_flow(self):
        """测试完整扫描流程"""
        # Mock查询结果
        available_courts = {
            '1号场地': ['18:00-19:00', '19:00-20:00'],
            '2号场地': ['20:00-21:00']
        }
        self.pipeline.query_manager.query_available_courts.return_value = available_courts
        self.pipeline.booking_manager.book_court.return_value = True
        
        self.pipeline._perform_scan()
        
        # 验证调用链
        self.pipeline.query_manager.query_available_courts.assert_called_once()
        self.pipeline.booking_manager.book_court.assert_called_once()
        self.pipeline.email_notifier.send_booking_success.assert_called_once()
    
    def test_perform_scan_no_available_courts(self):
        """测试扫描流程 - 无可用场地"""
        self.pipeline.query_manager.query_available_courts.return_value = {}
        
        self.pipeline._perform_scan()
        
        self.pipeline.booking_manager.book_court.assert_not_called()
        self.pipeline.email_notifier.send_booking_success.assert_not_called()
    
    def test_perform_scan_no_valid_courts(self):
        """测试扫描流程 - 无符合条件场地"""
        available_courts = {
            '1号场地': ['20:00-21:00'],  # 不符合条件
        }
        self.pipeline.query_manager.query_available_courts.return_value = available_courts
        
        self.pipeline._perform_scan()
        
        self.pipeline.booking_manager.book_court.assert_not_called()
    
    # ==================== 运行控制测试 ====================
    
    @patch('time.sleep', return_value=None)  # Mock sleep加速测试
    def test_run_scan_loop_single_iteration(self, mock_sleep):
        """测试扫描循环 - 单次迭代"""
        self.pipeline._perform_scan = Mock()
        self.pipeline.scan_interval = 1
        
        # 设置为运行一次后停止
        def stop_after_first_scan(*args, **kwargs):
            self.pipeline.is_running = False
        
        self.pipeline._perform_scan.side_effect = stop_after_first_scan
        
        self.pipeline._run_scan_loop()
        
        self.pipeline._perform_scan.assert_called_once()
    
    def test_stop(self):
        """测试停止扫描"""
        self.pipeline.is_running = True
        self.pipeline.stop()
        self.assertFalse(self.pipeline.is_running)
        self.pipeline.logger.info.assert_called()


class TestPeriodicScanningPipelineIntegration(unittest.TestCase):
    """集成测试 - 测试与其他模块的交互"""
    
    def setUp(self):
        """设置集成测试环境"""
        # 使用patch.object避免调用真实的__init__
        with patch.object(PeriodicScanningPipeline, '__init__', lambda x: None):
            self.pipeline = PeriodicScanningPipeline()
            self._setup_mocks()
    
    def _setup_mocks(self):
        """设置完整的Mock环境"""
        # Mock所有依赖
        self.pipeline.config = Mock()
        self.pipeline.logger = Mock()
        self.pipeline.query_manager = Mock()
        self.pipeline.booking_manager = Mock()
        self.pipeline.email_notifier = Mock()
        self.pipeline.start_pipeline = Mock()
        
        # 设置默认配置
        def mock_config_get(key, default=None):
            config_map = {
                'polling.polling_interval': 5,
                'polling.polling_time_slots': ['18:00-19:00'],
                'polling.polling_date': '2025-12-15',
                'polling.is_auto_booking': True,
                'booking.partner_name': '测试',
                'booking.venue_type': '羽毛球',
                'user.email': 'test@test.com'
            }
            return config_map.get(key, default)
        
        self.pipeline.config.get = Mock(side_effect=mock_config_get)
        
        # 初始化属性
        self.pipeline.scan_interval = 5
        self.pipeline.polling_time_slots = ['18:00-19:00']
        self.pipeline.polling_date = '2025-12-15'
        self.pipeline.is_auto_booking = True
        self.pipeline.partner_name = '测试'
        self.pipeline.venue_type = '羽毛球'
        self.pipeline.is_running = False
        self.pipeline.is_send_error_email = False
    
    def test_integration_successful_booking_flow(self):
        """集成测试 - 成功预订流程"""
        # 设置场景：有可用场地，预订成功
        self.pipeline.query_manager.query_available_courts.return_value = {
            '1号场地': ['18:00-19:00', '19:00-20:00']
        }
        self.pipeline.booking_manager.book_court.return_value = True
        
        # 执行单次扫描
        self.pipeline._perform_scan()
        
        # 验证完整流程
        self.pipeline.query_manager.query_available_courts.assert_called_once()
        self.pipeline.booking_manager.book_court.assert_called_once()
        self.pipeline.email_notifier.send_booking_success.assert_called_once()
        
        # 验证邮件内容
        call_args = self.pipeline.email_notifier.send_booking_success.call_args
        self.assertEqual(call_args[1]['to_email'], 'test@test.com')
        self.assertIn('court_name', call_args[1]['booking_info'])


def run_tests():
    """运行所有测试"""
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试
    suite.addTests(loader.loadTestsFromTestCase(TestPeriodicScanningPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestPeriodicScanningPipelineIntegration))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 输出统计
    print("\n" + "=" * 70)
    print(f"测试完成！")
    print(f"运行测试数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)