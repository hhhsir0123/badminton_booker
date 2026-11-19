"""SubmitCaptchaFlow 的纯逻辑测试（不启动浏览器）。"""
import os
import sys
import unittest
from unittest.mock import Mock, patch

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
        driver = self.FakeDriver({"return window.__bbVerify": {"verification": "V", "token": "T"},
                                  "form.submitData": "sent"})
        flow = SubmitCaptchaFlow(driver)
        flow.presolved = PreSolvedCaptcha(verification="V", token="T",
                                          form={"venueSiteId": 1})
        self.assertTrue(flow.submit({"reservationDate": "2026-11-21"}))
        self.assertTrue(driver.calls)
        self.assertIsNone(flow.presolved)

    def test_ready_rejects_missing_token_and_page_refresh(self):
        flow = SubmitCaptchaFlow(self.FakeDriver())
        flow.presolved = PreSolvedCaptcha(verification="V", token="")
        self.assertFalse(flow.ready_for_submission())
        flow.presolved.token = "T"
        self.assertFalse(flow.ready_for_submission())

    def test_ready_checks_validity_through_opening(self):
        flow = SubmitCaptchaFlow(self.FakeDriver({
            "return window.__bbVerify": {"verification": "V", "token": "T"}}), ttl_seconds=170)
        flow.presolved = PreSolvedCaptcha(verification="V", token="T")
        flow.presolved.solved_at -= 160
        self.assertTrue(flow.ready_for_submission())
        self.assertFalse(flow.ready_for_submission(min_validity=15))

    def test_failed_pre_solve_restores_callback(self):
        flow = SubmitCaptchaFlow(self.FakeDriver(), solver=Mock())
        flow.patch = Mock(return_value=True)
        flow.read = Mock(return_value=None)
        flow.open = Mock(return_value=False)
        flow.discard = Mock()
        self.assertIsNone(flow.pre_solve(max_rounds=1))
        flow.discard.assert_called_once()

    def test_empty_component_opens_and_waits_for_image(self):
        flow = SubmitCaptchaFlow(self.FakeDriver(), solver=Mock())
        flow.patch = Mock(return_value=True)
        empty = {"image": ""}
        image = {"image": "data:image/png;base64,eA==", "words": ["字"]}
        flow.read = Mock(side_effect=[empty, empty, image, image])
        flow.open = Mock(return_value=True)
        flow.solver.solve.return_value = [(10, 10)]
        flow.click = Mock(return_value=True)
        flow.captured = Mock(return_value=PreSolvedCaptcha(verification="V", token="T"))
        with patch("src.core.submit_captcha.time.sleep"):
            self.assertIsNotNone(flow.pre_solve(max_rounds=1))
        flow.open.assert_called_once()

    def test_selected_submission_always_builds_current_form(self):
        flow = SubmitCaptchaFlow(self.FakeDriver())
        flow.ready_for_submission = Mock(return_value=True)
        flow.click_submit_button = Mock(return_value=True)
        flow.read_form = Mock(return_value={"spaceId": "new-selection"})
        flow.submit = Mock(return_value=True)
        self.assertTrue(flow.submit_selected())
        flow.click_submit_button.assert_called_once()
        flow.submit.assert_called_once_with({"spaceId": "new-selection"})


if __name__ == "__main__":
    unittest.main()
