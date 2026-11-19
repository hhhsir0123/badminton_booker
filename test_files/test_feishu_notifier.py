"""飞书通知测试：不联网，只验证签名、消息结构和失败处理。"""
import base64
import hashlib
import hmac
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.feishu_notifier import FeishuNotifier

WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/abc"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        pass


def recording_opener(payload, store):
    def opener(request, timeout=None):
        store.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse(payload)

    return opener


class SignTest(unittest.TestCase):
    def test_signature_matches_feishu_algorithm(self):
        notifier = FeishuNotifier(WEBHOOK, secret="s3cret")
        stamp = "1700000000"
        expected = base64.b64encode(
            hmac.new(f"{stamp}\ns3cret".encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        self.assertEqual(notifier.sign(stamp), expected)

    def test_no_secret_no_signature(self):
        self.assertEqual(FeishuNotifier(WEBHOOK).sign("1700000000"), "")

    def test_disabled_without_webhook(self):
        self.assertFalse(FeishuNotifier("").enabled)
        self.assertFalse(FeishuNotifier("").send_text("hi"))


class PayloadTest(unittest.TestCase):
    def test_availability_card(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, opener=recording_opener({"code": 0}, store))
        ok = notifier.send_availability(
            "2026-11-21", {"训练馆3号": ["19:00-20:00", "20:00-21:00"]}
        )
        self.assertTrue(ok)
        payload = store[0]
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["title"]["content"], "羽毛球空场提醒")
        body = payload["card"]["elements"][0]["text"]["content"]
        self.assertIn("2026-11-21", body)
        self.assertIn("训练馆3号", body)
        self.assertIn("19:00-20:00", body)

    def test_success_card_has_partners(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, opener=recording_opener({"code": 0}, store))
        notifier.send_booking_success("2026-11-21", "训练馆3号", ["19:00-20:00"], ["张三"])
        body = store[0]["card"]["elements"][0]["text"]["content"]
        self.assertIn("训练馆3号", body)
        self.assertIn("张三", body)

    def test_mention_all_in_card(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, mention_all=True, opener=recording_opener({"code": 0}, store))
        notifier.send_availability("2026-11-21", {"训练馆3号": ["19:00-20:00"]})
        body = store[0]["card"]["elements"][0]["text"]["content"]
        self.assertTrue(body.startswith("<at id=all></at>"))

    def test_mention_all_in_text(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, mention_all=True, opener=recording_opener({"code": 0}, store))
        notifier.send_text("测试")
        self.assertIn('<at user_id="all">所有人</at>', store[0]["content"]["text"])

    def test_signed_payload(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, secret="s", opener=recording_opener({"code": 0}, store))
        notifier.send_text("hi")
        self.assertIn("timestamp", store[0])
        self.assertTrue(store[0]["sign"])


class FailureTest(unittest.TestCase):
    def test_platform_error_is_not_raised(self):
        store = []
        notifier = FeishuNotifier(WEBHOOK, opener=recording_opener({"code": 19021, "msg": "sign match fail"}, store))
        self.assertFalse(notifier.send_text("hi"))
        self.assertIn("19021", notifier.last_error)

    def test_network_error_is_not_raised(self):
        def boom(request, timeout=None):
            raise OSError("network down")

        notifier = FeishuNotifier(WEBHOOK, opener=boom)
        self.assertFalse(notifier.send_text("hi"))
        self.assertIn("network down", notifier.last_error)


if __name__ == "__main__":
    unittest.main()
