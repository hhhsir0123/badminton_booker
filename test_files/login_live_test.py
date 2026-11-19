"""真机登录测试：全新 profile + 账密 + 自动识别登录验证码。"""
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.config import Config  # noqa: E402
from src.core.browser import BrowserManager  # noqa: E402
from src.core.login import LoginManager  # noqa: E402
from src.utils.captcha_solver import CaptchaSolver  # noqa: E402
from src.utils.ocr import CaptchaRecognizer  # noqa: E402

USERNAME = os.environ.get("BB_USER", "")
PASSWORD = os.environ.get("BB_PASS", "")


def main():
    config = Config(os.path.join(ROOT, "config.yaml"))
    url = config.get("login.url", "https://ggtypt.cumtb.edu.cn/venue/login")
    username = USERNAME or config.get("user.username", "")
    password = PASSWORD or config.get("user.password", "")
    if not password:
        print("没有密码：请用 BB_PASS 环境变量传入")
        return
    print("登录账号:", username, "密码长度:", len(password))

    profile = os.path.join(ROOT, ".chrome-login-test")
    shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(profile, exist_ok=True)

    browser = BrowserManager(headless=True, profile_dir=profile, project_root=ROOT)
    driver = browser.start()
    try:
        recognizer = CaptchaRecognizer()
        solver = CaptchaSolver(browser, recognizer, max_attempts=3)
        login = LoginManager(browser, recognizer, solver)

        started = time.monotonic()
        ok = login.ensure_logged_in(
            url=url,
            username=username,
            password=password,
            auto_captcha=True,
            max_retry=2,
        )
        cost = int((time.monotonic() - started) * 1000)
        print("登录结果:", "成功" if ok else "失败", f"({cost}ms)")
        print("当前 URL:", driver.current_url)
        body = (driver.execute_script(
            "return (document.body && document.body.innerText || '').slice(0, 240);") or "")
        print("页面片段:", " | ".join(body.split("\n")[:6]))
        driver.save_screenshot(os.path.join(ROOT, "login_test.png"))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
