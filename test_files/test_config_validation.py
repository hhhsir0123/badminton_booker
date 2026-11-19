"""validate_for_run 的测试：保存/启动前的严格校验。"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import DEFAULTS, validate_for_run


def base():
    data = copy.deepcopy(DEFAULTS)
    data["user"]["username"] = "13800138000"
    data["user"]["password"] = "secret"
    data["user"]["partner_name"] = "张三"
    data["captcha"]["provider"] = "local"
    return data


class ValidateForRunTest(unittest.TestCase):
    def test_defaults_pass(self):
        self.assertEqual(validate_for_run(base()), [])

    def test_missing_username(self):
        data = base()
        data["user"]["username"] = ""
        self.assertTrue(any("登录手机号" in item for item in validate_for_run(data)))

    def test_bad_phone(self):
        data = base()
        data["user"]["username"] = "12345"
        self.assertTrue(any("11 位" in item for item in validate_for_run(data)))

    def test_missing_password(self):
        data = base()
        data["user"]["password"] = ""
        self.assertTrue(any("登录密码" in item for item in validate_for_run(data)))

    def test_missing_partner(self):
        data = base()
        data["user"]["partner_name"] = ""
        self.assertTrue(any("默认同伴" in item for item in validate_for_run(data)))

    def test_bad_partner(self):
        data = base()
        data["user"]["partner_name"] = "!!"
        self.assertTrue(any("同伴" in item for item in validate_for_run(data)))

    def test_partner_ok(self):
        data = base()
        data["user"]["partner_name"] = "张三"
        self.assertEqual(validate_for_run(data), [])

    def test_ttshitu_without_credentials(self):
        data = base()
        data["captcha"]["provider"] = "ttshitu"
        data["captcha"]["ttshitu"] = {"username": "", "password": "", "type_id": 43, "timeout_seconds": 20}
        errors = validate_for_run(data)
        self.assertTrue(any("图鉴用户名" in item for item in errors))
        self.assertTrue(any("图鉴密码" in item for item in errors))

    def test_ttshitu_with_credentials(self):
        data = base()
        data["captcha"]["provider"] = "ttshitu"
        data["captcha"]["ttshitu"] = {"username": "u", "password": "p", "type_id": 43, "timeout_seconds": 20}
        self.assertEqual(validate_for_run(data), [])

    def test_ttshitu_bad_type_id(self):
        data = base()
        data["captcha"]["provider"] = "ttshitu"
        data["captcha"]["ttshitu"] = {"username": "u", "password": "p", "type_id": 999, "timeout_seconds": 20}
        self.assertTrue(any("题型" in item for item in validate_for_run(data)))

    def test_unknown_provider(self):
        data = base()
        data["captcha"]["provider"] = "magic"
        self.assertTrue(any("local 或 ttshitu" in item for item in validate_for_run(data)))


if __name__ == "__main__":
    unittest.main()
