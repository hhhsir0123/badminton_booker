"""直接调用场馆接口（带签名）。

签名算法来自前端 `/venue/js/chingo.*.js` 的 emitAjax：

    sign = MD5(secret + path + 依次拼接(排序后的 key+value) + timestamp + " " + secret)
    请求头：app-key / timestamp / sign / cgAuthorization

其中 path 是不含 `/venue-server` 前缀的接口路径，cgAuthorization 是页面
localStorage 里的 dataSix（JWT）。

注意：secret 和 app-key 来自前端打包文件，平台重新发版可能会变。这里把它们做成
可配置常量，并在签名失败时自动回退到「页面内刷新」的旧路径。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

API_PREFIX = "/venue-server"
APP_KEY = "8fceb735082b5a529312040b58ea780b"
SECRET = "c640ca392cd45fb3a55b00a63a86c618"


def signature(timestamp: int, path: str, data: Dict[str, Any], secret: str = SECRET) -> str:
    joined = secret + path
    for key in sorted(data.keys()):
        value = data[key]
        if isinstance(value, (dict, list)) or value in ("", None):
            continue
        joined += str(key) + str(value)
    joined += str(timestamp) + " " + secret
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


class ApiClient:
    """最小化的场馆接口客户端（只用到 GET/POST + 签名）。"""

    # reservationDateSpaceInfo 的每个场地行混合了场地元数据和按时间 ID 索引的场格。
    ROW_METADATA_KEYS = {"venueSpaceGroupId", "spaceName", "enSpaceName", "id", "venueSiteId"}

    def __init__(self, origin: str, token: str, timeout: float = 8.0):
        self.origin = origin.rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        # 验证码的校验状态可能绑在会话 cookie 上，带上浏览器的 cookie 更稳
        self.cookie_header = ""
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
        )

    def _headers(self, path: str, data: Dict[str, Any]) -> Dict[str, str]:
        stamp = int(time.time() * 1000)
        headers = {
            "app-key": APP_KEY,
            "timestamp": str(stamp),
            "sign": signature(stamp, path, data),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": self.user_agent,
            "Referer": self.origin + "/venue/",
        }
        if self.token:
            headers["cgAuthorization"] = self.token
            headers["cgappauthorization"] = self.token
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    def get(self, path: str, params: Dict[str, Any]) -> Tuple[int, str]:
        query = urllib.parse.urlencode(params)
        url = f"{self.origin}{API_PREFIX}{path}?{query}"
        request = urllib.request.Request(url, headers=self._headers(path, params))
        return self._open(request)

    def post(self, path: str, data: Dict[str, Any]) -> Tuple[int, str]:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = self._headers(path, data)
        headers["Content-Type"] = "application/json;charset=UTF-8"
        request = urllib.request.Request(
            f"{self.origin}{API_PREFIX}{path}", data=body, headers=headers, method="POST"
        )
        return self._open(request)

    def _open(self, request) -> Tuple[int, str]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")
        except Exception as error:  # noqa: BLE001
            return 0, str(error)

    def json_get(self, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        status, body = self.get(path, params)
        if status != 200:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("code") not in (200, "200"):
            return None
        return payload.get("data") or {}

    @classmethod
    def inspect_day_response(cls, status: int, body: str, day: str) -> Dict[str, Any]:
        """只保留可公开诊断字段；HTTP 成功不等于业务成功或目标日期已就绪。"""
        result: Dict[str, Any] = {"ok": False, "business_code": None,
                                  "target_date_present": False, "free_slots": 0}
        if status != 200:
            return dict(result, error="http-error" if status else "transport-error")
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return dict(result, error="invalid-json")
        if not isinstance(payload, dict):
            return dict(result, error="invalid-payload")
        code = payload.get("code")
        # 不回传平台原始 message，避免把敏感响应或不可信文本写入日志。
        result["business_code"] = code if isinstance(code, (int, str)) and len(str(code)) < 16 else None
        if code not in (200, "200"):
            return dict(result, error="business-error")
        data = payload.get("data")
        dates = data.get("reservationDateSpaceInfo") if isinstance(data, dict) else None
        if not isinstance(dates, dict):
            return dict(result, error="invalid-date-data")
        result["available_dates"] = [key for key in dates if isinstance(key, str)
                                     and len(key) == 10 and key[4:5] == "-" and key[7:8] == "-"]
        if day not in dates:
            return dict(result, error="target-date-missing")
        result["target_date_present"] = True
        rows = dates[day]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return dict(result, error="invalid-slot-data")
        for row in rows:
            for key, cell in row.items():
                if key in cls.ROW_METADATA_KEYS:
                    continue
                if not isinstance(cell, dict) or cell.get("reservationStatus") not in (1, 2, 3, 4):
                    return dict(result, error="invalid-slot-data")
                if cell.get("reservationStatus") == cls.STATUS_FREE:
                    start, end = cell.get("startDate"), cell.get("endDate")
                    name = cell.get("spaceName")
                    if (not isinstance(name, str) or not name.strip()
                            or not isinstance(start, str) or not start.startswith(day + " ")
                            or not isinstance(end, str) or not end.startswith(day + " ")
                            or len(start.split(" ")[-1]) < 5 or len(end.split(" ")[-1]) < 5):
                        return dict(result, error="invalid-slot-data")
        try:
            slots = cls.parse_slots(data, day)
        except (TypeError, ValueError, AttributeError):
            return dict(result, error="invalid-slot-data")
        return dict(result, ok=True, target_date_present=True,
                    free_slots=sum(len(items) for items in slots.values()), error=None)

    def free_slots(self, venue_site_id: str, day: str) -> Dict[str, Dict[str, str]]:
        """读某天的场格：{场地: {HH:MM-HH:MM: 原文}}，只保留真正可订的。"""
        data = self.json_get(
            "/api/reservation/day/info",
            {"venueSiteId": venue_site_id, "searchDate": day, "hasReserveInfo": "1",
             "nocache": str(int(time.time() * 1000))},
        )
        return self.parse_slots(data or {}, day)

    # reservationStatus 的真实含义（来自前端常量表）：
    #   1 FREE         可预约     class free
    #   2 DISABLED     不开放     class disabled
    #   3 RESERVATION  未支付     class reservation   ← 别人占着没付款
    #   4 RESERVED     已预约     class reserved
    # 多容量场馆还会按占用百分比再叠一层 class，所以不能只看 status。
    STATUS_FREE = 1
    STATUS_RESERVATION = 3
    STATUS_RESERVED = 4

    @classmethod
    def parse_slots(cls, data: Dict[str, Any], day: str) -> Dict[str, Dict[str, str]]:
        rows = (data.get("reservationDateSpaceInfo") or {}).get(day) or []
        slots: Dict[str, Dict[str, str]] = {}
        for row in rows:
            for _, cell in (row or {}).items():
                if not isinstance(cell, dict):
                    continue
                if cell.get("reservationStatus") != cls.STATUS_FREE:
                    continue
                # 已经有人占了名额（哪怕还没付款）就不碰：
                # 这类格子在前端会额外带上 reservation/reserved 的 class。
                if cell.get("alreadyNum") or cell.get("waitNum"):
                    continue
                court = (cell.get("spaceName") or "").strip()
                start, end = str(cell.get("startDate") or ""), str(cell.get("endDate") or "")
                if " " not in start or " " not in end or not court:
                    continue
                start_time, end_time = start.split(" ")[1][:5], end.split(" ")[1][:5]
                slots.setdefault(court, {})[f"{start_time}-{end_time}"] = f"{start_time}-{end_time}"
        return slots


def _form(data: Dict[str, Any]) -> str:
    """按前端口径序列化表单：空串保留、None 丢弃。"""
    pairs = []
    for key, value in data.items():
        if value is None:
            continue
        pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs)


def _post_form(self, path: str, data: Dict[str, Any]) -> Tuple[int, str]:
    body = _form(data).encode("utf-8")
    headers = self._headers(path, data)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(
        f"{self.origin}{API_PREFIX}{path}", data=body, headers=headers, method="POST"
    )
    return self._open(request)


ApiClient.post_form = _post_form
