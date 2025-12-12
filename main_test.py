"""
主程序入口
"""
import sys
import os
import time

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline
from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline

def test_periodic_scanning_pipeline():
    """测试定时轮询抢票流程"""
    pipeline = PeriodicScanningPipeline()
    pipeline.run()

def test_ticket_grabbing_pipeline():
    """测试定时抢票流程"""
    pipeline = TicketGrabbingPipeline()
    pipeline.run()

if __name__ == "__main__":
    """主函数入口"""
    test_ticket_grabbing_pipeline()


