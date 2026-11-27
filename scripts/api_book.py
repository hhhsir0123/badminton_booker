"""纯接口下单（PoC）：用乒乓球验证整条链路，不点页面提交。

    .venv/bin/python -u scripts/api_book.py 1 2026-11-29 气膜馆1号 08:00-09:00
"""
from __future__ import annotations

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.config import Config  # noqa: E402
from src.core.api_booking import (  # noqa: E402
    check_captcha, decode_captcha_image, fetch_captcha, submit_order,
)
from src.core.browser import BrowserManager  # noqa: E402
from src.core.court_query import QueryManager  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402
from src.utils.ttshitu_provider import TTShituProvider  # noqa: E402

logger = setup_logger("api_book")


def main() -> int:
    site = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    day = sys.argv[2] if len(sys.argv) > 2 else "2026-11-29"
    want_court = sys.argv[3] if len(sys.argv) > 3 else ""
    want_slot = sys.argv[4] if len(sys.argv) > 4 else ""

    config = Config(os.path.join(PROJECT_ROOT, "config.yaml"))
    browser = BrowserManager(headless=True, profile_dir="/tmp/poc-profile3", project_root=PROJECT_ROOT)
    driver = browser.start()
    try:
        driver.get(f"https://ggtypt.cumtb.edu.cn/venue/venue-reservation/{site}")
        time.sleep(5)
        query = QueryManager(browser)
        query.install()
        client = query._api_client()
        try:
            client.user_agent = driver.execute_script("return navigator.userAgent") or client.user_agent
            logger.info("使用浏览器 UA：%s", client.user_agent[:60])
        except Exception:
            pass
        try:
            cookies = driver.get_cookies() or []
            client.cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            logger.info("带上 %s 个浏览器 cookie", len(cookies))
        except Exception as error:  # noqa: BLE001
            logger.debug("读取 cookie 失败: %s", error)
        data = client.json_get("/api/reservation/day/info", {
            "venueSiteId": str(site), "searchDate": day,
            "hasReserveInfo": "1", "nocache": str(int(time.time() * 1000))}) or {}
        rows = (data.get("reservationDateSpaceInfo") or {}).get(day) or []
        times = {f"{t['beginTime']}-{t['endTime']}": t["id"] for t in (data.get("spaceTimeInfo") or [])}
        price = (data.get("siteParam") or {}).get("orderPrice")
        buddy = ((data.get("orderParamView") or {}).get("buddyList") or [{}])[0].get("id")

        target = None
        for row in rows:
            # 真正的 spaceId 是行级元数据 id，不是场格字典的 key
            space_id = row.get("id")
            for key, cell in row.items():
                if not isinstance(cell, dict) or cell.get("reservationStatus") != 1:
                    continue
                slot = f"{cell['startDate'][11:16]}-{cell['endDate'][11:16]}"
                if want_court and cell.get("spaceName") != want_court:
                    continue
                if want_slot and slot != want_slot:
                    continue
                target = (space_id, cell, slot)
                break
            if target:
                break
        if not target:
            for row in rows:
                space_id = row.get("id")
                for key, cell in row.items():
                    if isinstance(cell, dict) and cell.get("reservationStatus") == 1:
                        slot = f"{cell['startDate'][11:16]}-{cell['endDate'][11:16]}"
                        target = (space_id, cell, slot)
                        break
                if target:
                    break
        if not target:
            logger.warning("没有可用场格")
            return 1
        space_id, cell, slot = target
        logger.info("目标：%s %s spaceId=%s timeId=%s", cell.get("spaceName"), slot, space_id, times.get(slot))

        try:
            uid = driver.execute_script("return localStorage.getItem('point') || ''")
        except Exception:
            uid = ""
        if uid:
            rep = fetch_captcha(client, client_uid=f"point-{uid}")
        else:
            rep = fetch_captcha(client)
        if not rep:
            return 1
        image = decode_captcha_image(rep)
        with open("/tmp/api_captcha.png", "wb") as fh:
            fh.write(image)
        words = rep.get("wordList") or []
        points = None
        try:
            from src.utils.click_word_solver import ClickWordSolver
            local = ClickWordSolver(fallback_to_local=True)
            points = local.solve(image, words)
            logger.info("本地模板匹配坐标：%s", points)
        except Exception as error:  # noqa: BLE001
            logger.warning("本地识别失败: %s", error)
        if not points:
            provider = TTShituProvider.from_config(config)
            samples = []
            for _ in range(1):
                try:
                    got = provider.solve(image, words=words)
                except Exception:  # noqa: BLE001
                    continue
                if got and len(got) == len(words):
                    samples.append(got)
                time.sleep(0.15)
            if samples:
                # 多打几次取中位数，压低单次偏移
                import statistics
                points = [
                    (int(statistics.median(s[i][0] for s in samples)),
                     int(statistics.median(s[i][1] for s in samples)))
                    for i in range(len(samples[0]))
                ]
                logger.info("多采样中位数坐标：%s（%s 次）", points, len(samples))
            else:
                points = provider.solve(image, words=words)
        logger.info("识别坐标：%s", points)

        result = check_captcha(client, rep, points)
        if not result:
            return 1
        logger.info("验证码通过：req_token=%s resp_token=%s resp_pointJson=%s",
                    rep["token"][:8], (result["response"].get("token") or "")[:8],
                    (result["response"].get("pointJson") or "")[:8])

        payload = {
            "captchaVerification": result["variants"][2],
            "captchaToken": rep["token"],
            "reservationOrderJson": json.dumps([{"spaceId": str(space_id), "timeId": str(times[slot])}],
                                               separators=(",", ":")),
            "reservationDate": day,
            "weekStartDate": day,
            "reservationType": -1,
            "orderPrice": cell.get("orderFee") or cell.get("price") or price or 0,
            "orderPin": __import__("src.core.order_pin", fromlist=["build_order_pin"]).build_order_pin(512, 384),
            "venueSiteId": site,
            "phone": str(config.get("user.username")),
            "buddyUids": "",
            "buddyIds": str(buddy or ""),
        }
        status, body = submit_order(client, payload)
        logger.info("下单返回 HTTP %s：%s", status, (body or "")[:200])
        tries = 0
        while '"code":250' in (body or "") and "非法校验" in (body or "") and tries < 3:
            tries += 1
            logger.info("验证码校验未过，换一张重试（第 %s 次）", tries)
            rep = fetch_captcha(client)
            if not rep:
                break
            img = decode_captcha_image(rep)
            pts = provider.solve(img, words=rep.get("wordList") or [])
            res = check_captcha(client, rep, pts)
            if not res:
                continue
            payload["captchaVerification"] = res["variants"][2]
            payload["captchaToken"] = rep["token"]
            status, body = submit_order(client, payload)
            logger.info("重试下单返回 HTTP %s：%s", status, (body or "")[:200])
        index = 0
        while '"code":511' in (body or "") and index + 1 < len(result["variants"]):
            index += 1
            payload["captchaVerification"] = result["variants"][index]
            logger.info("换一种 captchaVerification 拼法重试（variant %s）", index)
            status, body = submit_order(client, payload)
            logger.info("下单返回 HTTP %s：%s", status, (body or "")[:200])
        return 0 if '"code":200' in (body or "") else 1
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
