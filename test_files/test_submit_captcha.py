"""SubmitCaptchaFlow 的纯逻辑测试（不启动浏览器）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.submit_captcha import PreSolvedCaptcha, SubmitCaptchaFlow


class PreSolvedTest(unittest.TestCase):
    def test_payload_merges_form_and_token(self):
        captcha = PreSolvedCaptcha(verification="V", token="T",
                                   form={"venueSiteId": 1, "phone": "123"})
        payload = captcha.payload()
        self.assertEqual(payload["venueSiteId"], 1)
        self.assertEqual(payload["captchaVerification"], "V")
        self.assertEqual(payload["captchaToken"], "T")

    def test_overrides_win(self):
        captcha = PreSolvedCaptcha(verification="V", token="T",
                                   form={"reservationDate": "2026-11-21"})
        payload = captcha.payload({"reservationDate": "2026-11-22"})
        self.assertEqual(payload["reservationDate"], "2026-11-22")

    def test_age(self):
        captcha = PreSolvedCaptcha(verification="V", token="T")
        self.assertLess(captcha.age(), 5)


class FlowTest(unittest.TestCase):
    class FakeDriver:
        def __init__(self, responses=None):
            self.responses = responses or {}
            self.calls = []

        def execute_script(self, script, *args):
            self.calls.append(script[:40])
            for key, value in self.responses.items():
                if key in script:
                    return value
            return None

    def test_submit_requires_presolved(self):
        flow = SubmitCaptchaFlow(self.FakeDriver())
        self.assertFalse(flow.submit())

    def test_submit_expired_token_rejected(self):
        flow = SubmitCaptchaFlow(self.FakeDriver({"form.submitData": "sent"}))
        flow.presolved = PreSolvedCaptcha(verification="V", token="T", form={})
        flow.presolved.solved_at -= 10000
        self.assertFalse(flow.submit())

    def test_submit_sends_payload(self):
        driver = self.FakeDriver({"form.submitData": "sent"})
        flow = SubmitCaptchaFlow(driver)
        flow.presolved = PreSolvedCaptcha(verification="V", token="T",
                                          form={"venueSiteId": 1})
        self.assertTrue(flow.submit({"reservationDate": "2026-11-21"}))
        self.assertTrue(driver.calls)


if __name__ == "__main__":
    unittest.main()
