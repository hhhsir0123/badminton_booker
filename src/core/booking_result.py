"""抢场结果。

订单是否生成只能由平台回执判断，所以这里区分四种状态，
调用方必须显式分支，避免「点了提交就当成功」。
"""
from enum import Enum


class BookingOutcome(Enum):
    CREATED = "created"        # 平台已建单（含待支付）
    UNAVAILABLE = "unavailable"  # 场格已被抢走/不可选，可安全换下一个候选
    REJECTED = "rejected"      # 平台明确拒绝（重复预约、超出限额等）
    UNKNOWN = "unknown"        # 无法确认，必须人工核对，禁止自动重试

    def __bool__(self):
        return self is BookingOutcome.CREATED
