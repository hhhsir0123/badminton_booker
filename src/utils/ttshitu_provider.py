"""图鉴（ttshitu）打码平台客户端。

接口：POST http://api.ttshitu.com/predict
参数：username / password / typeid / image(base64)
点选类型题号：
    19 = 点选 1 个坐标
    20 = 点选 3 个坐标      ← 本项目用这个（AJ-Captcha 点选 3 个字）
    21 = 点选 3~5 个坐标
    22 = 点选 5~8 个坐标
返回：{"success": true, "data": {"result": "x1,y1|x2,y2|x3,y3"}}

注意：调用会产生费用；失败时抛 TTShituError，由调用方决定是否回落到本地 OCR。
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

API_URL = "http://api.ttshitu.com/predict"

# 常见错误码（平台文档）
ERROR_CODES: Dict[str, str] = {
    "1001": "参数错误",
    "1002": "余额不足，请先充值",
    "1003": "账号或密码错误",
    "1004": "图片太大",
    "1005": "上传失败",
    "1006": "识别失败",
    "1007": "无此类型（检查 typeid）",
}


class TTShituError(RuntimeError):
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


def parse_points(result: str) -> List[Tuple[int, int]]:
    """把 "x1,y1|x2,y2|x3,y3" 解析成坐标列表，兼容中文逗号和空格。"""
    points: List[Tuple[int, int]] = []
    for chunk in str(result).replace("；", ";").replace(";", "|").split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.replace("，", ",").replace(" ", "").split(",")
        if len(parts) < 2:
            continue
        try:
            points.append((int(float(parts[0])), int(float(parts[1]))))
        except ValueError:
            continue
    return points


class TTShituProvider:
    def __init__(self, username: str, password: str, type_id: int = 20, timeout: float = 8.0):
        self.username = username or ""
        self.password = password or ""
        self.type_id = int(type_id)
        self.timeout = float(timeout)

    def check_credentials(self) -> None:
        if not self.username or not self.password:
            raise TTShituError("图鉴账号或密码为空（config.yaml 的 captcha.ttshitu）")

    @classmethod
    def from_config(cls, config) -> "TTShituProvider":
        return cls(
            username=config.get("captcha.ttshitu.username", ""),
            password=config.get("captcha.ttshitu.password", ""),
            type_id=config.get("captcha.ttshitu.type_id", 20),
            timeout=config.get("captcha.ttshitu.timeout_seconds", 8.0),
        )

    def payload(self, image_bytes: bytes, type_id: Optional[int] = None,
                content: Optional[str] = None) -> Dict[str, str]:
        data = {
            "username": self.username,
            "password": self.password,
            "typeid": str(type_id if type_id is not None else self.type_id),
            "image": base64.b64encode(image_bytes).decode("ascii"),
        }
        # typeid=43：传标题文本做闭集匹配，中文要转成 \uXXXX
        if content:
            data["content"] = "".join("\\u%04x" % ord(ch) for ch in content)
        return data

    def solve(self, image_bytes: bytes, type_id: Optional[int] = None,
              words=None) -> List[Tuple[int, int]]:
        self.check_credentials()
        content = "".join(str(w) for w in words) if words else None
        data = json.dumps(self.payload(image_bytes, type_id, content)).encode("utf-8")
        request = urllib.request.Request(
            API_URL, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.URLError as error:
            raise TTShituError(f"网络错误: {error}") from error
        except TimeoutError as error:
            raise TTShituError(f"请求超时: {error}") from error

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise TTShituError(f"返回不是 JSON: {body[:120]!r}") from error

        if not parsed.get("success"):
            code = str(parsed.get("code", ""))
            message = ERROR_CODES.get(code) or parsed.get("message") or "识别失败"
            raise TTShituError(message, code)

        result = (parsed.get("data") or {}).get("result", "")
        points = parse_points(result)
        if not points:
            raise TTShituError(f"返回坐标为空: {result!r}")
        return points
