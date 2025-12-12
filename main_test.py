"""
主程序入口
"""
import sys
import os
import time

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline


if __name__ == "__main__":
    """主函数入口"""
    # 选择运行的流程
    # 这里以定时轮询流程为例，实际使用中可以根据需要选择不同的流程
    pipeline = PeriodicScanningPipeline()
    pipeline.run()


