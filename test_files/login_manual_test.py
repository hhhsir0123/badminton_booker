"""手动走一遍登录：填账号密码 + 识别验证码 + 提交，然后打印页面报错。"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.core.browser import BrowserManager  # noqa: E402
from src.utils.ocr import CaptchaRecognizer  # noqa: E402

USERNAME = os.environ.get("BB_USER", "17396154630")
PASSWORD = os.environ.get("BB_PASS", "hhhSir012345")


def main():
    profile = os.path.join(ROOT, ".chrome-login-test")
    browser = BrowserManager(headless=True, profile_dir=profile, project_root=ROOT)
    driver = browser.start()
    recognizer = CaptchaRecognizer()
    try:
        driver.get("https://ggtypt.cumtb.edu.cn/venue/login")
        time.sleep(2)
        for el in driver.find_elements("xpath", "//*[contains(text(), '校外用户')]"):
            if el.is_displayed():
                el.click()
                break
        time.sleep(2)

        inputs = [i for i in driver.find_elements("css selector", "input") if i.is_displayed()]
        print("可见输入框:", [(i.get_attribute("type"), i.get_attribute("placeholder")) for i in inputs])

        # 账号 / 密码 / 验证码
        user_box = next((i for i in inputs if i.get_attribute("type") in ("text", "tel")), None)
        pass_box = next((i for i in inputs if i.get_attribute("type") == "password"), None)
        code_box = None
        for i in inputs:
            if i in (user_box, pass_box):
                continue
            if i.get_attribute("type") in ("text", "tel") and i.get_attribute("maxlength") in ("4", "6"):
                code_box = i
                break
        if code_box is None:
            candidates = [i for i in inputs if i not in (user_box, pass_box) and i.get_attribute("type") in ("text", "tel")]
            code_box = candidates[0] if candidates else None

        img = None
        for item in driver.find_elements("css selector", "img"):
            if item.is_displayed() and item.size["width"] >= 40 and item.size["width"] <= 140:
                img = item
                break
        path = os.path.join(ROOT, "login_captcha.png")
        if img:
            img.screenshot(path)
            code = recognizer.ocr.classification(open(path, "rb").read())
        else:
            code = ""
        print("识别到的验证码:", repr(code))

        user_box.clear(); user_box.send_keys(USERNAME)
        pass_box.clear(); pass_box.send_keys(PASSWORD)
        if code_box and code:
            code_box.clear(); code_box.send_keys(code)
        print("已填表，提交…")

        buttons = [b for b in driver.find_elements("css selector", "button, .ivu-btn") if b.is_displayed()]
        for b in buttons:
            if "登录" in (b.text or ""):
                b.click()
                break
        time.sleep(3)

        print("URL:", driver.current_url)
        body = driver.execute_script("return (document.body && document.body.innerText || '').slice(0, 400);") or ""
        print("页面:", " | ".join(line for line in body.split("\n") if line.strip())[:400])
        driver.save_screenshot(os.path.join(ROOT, "login_after.png"))
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
