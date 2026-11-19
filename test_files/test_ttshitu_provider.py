"""图鉴 provider 的离线测试：全部用 mock，不产生真实请求和费用。"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.ttshitu_provider import (
    TTShituError,
    TTShituProvider,
    parse_points,
)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ParsePointsTest(unittest.TestCase):
    def test_three_points(self):
        self.assertEqual(parse_points("12,34|56,78|90,12"), [(12, 34), (56, 78), (90, 12)])

    def test_trailing_pipe_and_spaces(self):
        self.assertEqual(parse_points(" 12, 34 |56,78| "), [(12, 34), (56, 78)])

    def test_chinese_comma(self):
        self.assertEqual(parse_points("12，34|56，78"), [(12, 34), (56, 78)])

    def test_empty(self):
        self.assertEqual(parse_points(""), [])


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.provider = TTShituProvider("user", "pass", type_id=20)

    def test_payload_contains_base64_and_typeid(self):
        payload = self.provider.payload(b"\x89PNG", None)
        self.assertEqual(payload["typeid"], "20")
        self.assertEqual(payload["username"], "user")

    def test_success(self):
        fake = FakeResponse({"success": True, "code": "0",
                             "data": {"result": "10,20|30,40|50,60"}})
        with mock.patch("src.utils.ttshitu_provider.urllib.request.urlopen", return_value=fake):
            points = self.provider.solve(b"image")
        self.assertEqual(points, [(10, 20), (30, 40), (50, 60)])

    def test_balance_error(self):
        fake = FakeResponse({"success": False, "code": "1002", "message": "余额不足"})
        with mock.patch("src.utils.ttshitu_provider.urllib.request.urlopen", return_value=fake):
            with self.assertRaises(TTShituError) as ctx:
                self.provider.solve(b"image")
        self.assertIn("余额不足", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "1002")

    def test_empty_result(self):
        fake = FakeResponse({"success": True, "data": {"result": ""}})
        with mock.patch("src.utils.ttshitu_provider.urllib.request.urlopen", return_value=fake):
            with self.assertRaises(TTShituError):
                self.provider.solve(b"image")

    def test_missing_credentials(self):
        with self.assertRaises(TTShituError):
            TTShituProvider("", "").solve(b"image")


if __name__ == "__main__":
    unittest.main()
