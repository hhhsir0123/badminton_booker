"""飞书群机器人通知。

用的是「自定义机器人」的 Incoming Webhook：往群里加一个机器人，拿到
https://open.feishu.cn/open-apis/bot/v2/hook/xxxx 这样的地址，POST 一段 JSON 即可。

只依赖标准库，不引入 requests / 飞书 SDK：
- 支持签名校验（机器人开启「签名校验」时，把 secret 填进配置）；
- 支持 @所有人；
- 失败不抛异常、只记日志：抢场主流程绝不能被通知卡住。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .logger import setup_logger

logger = setup_logger(__name__)

GREEN = "green"
BLUE = "blue"
RED = "red"


class FeishuNotifier:
    """飞书自定义机器人。"""

    def __init__(
        self,
        webhook: str,
        secret: str = "",
        mention_all: bool = False,
        timeout: float = 6.0,
        opener=None,
    ):
        self.webhook = (webhook or "").strip()
        self.secret = (secret or "").strip()
        self.mention_all = bool(mention_all)
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return self.webhook.startswith("http")

    # ------------------------------------------------------------- 协议

    def sign(self, timestamp: str) -> str:
        """飞书签名：HMAC-SHA256(key='timestamp\\nsecret', msg='') 再 base64。"""
        if not self.secret:
            return ""
        key = f"{timestamp}\n{self.secret}".encode("utf-8")
        digest = hmac.new(key, digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _auth(self) -> Dict[str, Any]:
        if not self.secret:
            return {}
        stamp = str(int(time.time()))
        return {"timestamp": stamp, "sign": self.sign(stamp)}

    def _post(self, payload: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.webhook,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        for attempt in range(2):
            try:
                response = self._opener(request, timeout=self.timeout)
                try:
                    raw = response.read()
                finally:
                    close = getattr(response, "close", None)
                    if close:
                        close()
                data = json.loads(raw.decode("utf-8") or "{}")
                code = data.get("code", data.get("StatusCode", 0))
                if code in (0, "0", None):
                    return True
                self.last_error = f"code={code} msg={data.get('msg', '')}"
                logger.warning("飞书返回错误: %s", self.last_error)
                return False
            except Exception as error:  # noqa: BLE001 - 通知失败不能影响主流程
                self.last_error = str(error)
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                logger.warning("飞书通知发送失败: %s", error)
        return False

    # ------------------------------------------------------------- 消息

    def _card(self, title: str, body: str, template: str, note: str = "") -> Dict[str, Any]:
        content = body
        if self.mention_all:
            content = "<at id=all></at>\n" + content
        elements: List[Dict[str, Any]] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
        ]
        if note:
            elements.append({"tag": "hr"})
            elements.append(
                {"tag": "note", "elements": [{"tag": "plain_text", "content": note}]}
            )
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
                "elements": elements,
            },
            **self._auth(),
        }

    def send_text(self, text: str) -> bool:
        content = text
        if self.mention_all:
            content = '<at user_id="all">所有人</at> ' + text
        return self._post({"msg_type": "text", "content": {"text": content}, **self._auth()})

    def send_availability(self, day: str, courts: Dict[str, List[str]], note: str = "") -> bool:
        """轮询发现空场。"""
        lines = [f"**{day}** 有空场："]
        for court, slots in courts.items():
            lines.append(f"- {court}：{'、'.join(slots)}")
        body = "\n".join(lines)
        return self._post(
            self._card(
                "羽毛球空场提醒",
                body,
                GREEN,
                note or "来自羽毛球预约助手 · 请尽快到场馆平台下单",
            )
        )

    def send_booking_success(
        self, day: str, court: str, slots: List[str], partners: Optional[List[str]] = None
    ) -> bool:
        lines = [
            f"**日期**：{day}",
            f"**场地**：{court}",
            f"**时段**：{'、'.join(slots)}",
        ]
        if partners:
            lines.append(f"**同伴**：{'、'.join(partners)}")
        return self._post(
            self._card(
                "羽毛球预订成功",
                "\n".join(lines),
                BLUE,
                "订单已生成，请在浏览器窗口里完成付款",
            )
        )

    def send_failure(self, message: str) -> bool:
        return self._post(self._card("羽毛球预约未成功", message, RED, "详见运行日志"))
