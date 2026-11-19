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

    def __init__(self, origin: str, token: str, timeout: float = 8.0):
        self.origin = origin.rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def _headers(self, path: str, data: Dict[str, Any]) -> Dict[str, str]:
        stamp = int(time.time() * 1000)
        headers = {
            "app-key": APP_KEY,
            "timestamp": str(stamp),
            "sign": signature(stamp, path, data),
            "Accept": "application/json, text/plain, */*",
            "Referer": self.origin + "/venue/",
        }
        if self.token:
            headers["cgAuthorization"] = self.token
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
        if payload.get("code") not in (200, "200"):
            return None
        return payload.get("data") or {}

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
