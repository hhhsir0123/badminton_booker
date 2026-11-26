"""orderPin 的算法复现（纯接口下单 PoC）。

前端 chunk-c3869bc6 里：
    var s = this.commonMethods.keySetPassword(e.clientX + "," + e.clientY)
    ... orderPin: s

keySetPassword（chingo.js 里的 ee 函数）是 AES-CBC/PKCS7：
    key = R.join("")，iv = L.join("")（来自 school-config/cumt.js 的 k / v 数组）
    ciphertext.toString() 输出 hex

所以 orderPin 不是服务端下发的 token，而是「点击坐标」的 AES 密文，
只要知道 key/iv，就能在纯 Python 里算出来。
"""
from __future__ import annotations

from typing import List

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# school-config/cumt.js: k / v 两个数组按顺序拼接
KEY_PARTS: List[str] = ["c1h", "2i", "5n6g", "2o", "2k", "4a7"]
IV_PARTS: List[str] = ["C2", "H3", "I4N", "5G2O", "3K", "1E4"]

KEY = "".join(KEY_PARTS)
IV = "".join(IV_PARTS)


def build_order_pin(x: int, y: int) -> str:
    """复现前端 keySetPassword(`${x},${y}`)：返回 32 位 hex。"""
    plain = f"{x},{y}".encode("utf-8")
    cipher = AES.new(KEY.encode("utf-8"), AES.MODE_CBC, IV.encode("utf-8"))
    return cipher.encrypt(pad(plain, AES.block_size)).hex()


def decrypt_order_pin(pin: str) -> str:
    """反向解开（仅用于自测/核对，线上不需要）。"""
    cipher = AES.new(KEY.encode("utf-8"), AES.MODE_CBC, IV.encode("utf-8"))
    raw = cipher.decrypt(bytes.fromhex(pin))
    pad_len = raw[-1]
    return raw[:-pad_len].decode("utf-8")


def build_submit_payload(
    *,
    venue_site_id: int,
    day: str,
    space_id: str,
    time_id: str,
    price,
    buddy_ids: str = "",
    phone: str = "",
    click: tuple = (512, 384),
) -> dict:
    """拼出 /api/reservation/order/submit 的 payload（与前端字段一一对应）。

    只拼数据，不发请求；用于 PoC 验证字段是否齐全。
    """
    order_json = '[{"spaceId":"%s","timeId":"%s"}]' % (space_id, time_id)
    return {
        "venueSiteId": venue_site_id,
        "reservationDate": day,
        "weekStartDate": day,
        "reservationOrderJson": order_json,
        "orderPin": build_order_pin(int(click[0]), int(click[1])),
        "phone": phone,
        "buddyIds": buddy_ids,
        "orderPrice": price,
        "reservationType": -1,
        "totalAmt": None,
    }
