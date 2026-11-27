"""纯接口下单（PoC）：不点页面，直接走验证码 + 下单接口。

链路（全部已在真机抓包验证）：

1. GET  /api/captcha/get?captchaType=clickWord&clientUid=point-<uuid>&ts=..&nocache=..
   → data.repData.{secretKey, originalImageBase64, token}
2. 识别图片里的 3 个点 → [{x, y}, ...]
3. pointJson = base64(AES-ECB-PKCS7(secretKey, JSON(points)))
4. POST /api/captcha/check (form): captchaType=clickWord&pointJson=..&token=..
   → data.repData.{result, pointJson, token}
5. captchaVerification = base64(AES-ECB-PKCS7(secretKey, token + "---" + pointJson))
6. POST /api/reservation/order/submit (form, 带签名)
"""
from __future__ import annotations

import base64
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

CAPTCHA_TYPE = "clickWord"


def _aes_b64(secret_key: str, plain: str) -> str:
    cipher = AES.new(secret_key.encode("utf-8"), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(plain.encode("utf-8"), 16))).decode()


def build_point_json(secret_key: str, points: List[Tuple[int, int]]) -> str:
    payload = json.dumps([{"x": int(x), "y": int(y)} for x, y in points], separators=(",", ":"))
    return _aes_b64(secret_key, payload)


def build_captcha_verification(secret_key: str, token: str, point_json: str) -> str:
    return _aes_b64(secret_key, f"{token}---{point_json}")


def fetch_captcha(client, client_uid: str = "") -> Optional[Dict[str, Any]]:
    """拿一张点选验证码。"""
    params = {
        "captchaType": CAPTCHA_TYPE,
        "clientUid": client_uid or f"point-{uuid.uuid4()}",
        "ts": int(__import__("time").time() * 1000),
        "nocache": int(__import__("time").time() * 1000),
    }
    data = client.json_get("/api/captcha/get", params) or {}
    # 两种返回形状都要兼容：{code:200,data:{repCode,repData}} 或直接的 {repCode,repData}
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if data.get("code") not in (None, 200):
        logger.warning("获取验证码失败: code=%s msg=%s", data.get("code"), data.get("message"))
        return None
    if str(inner.get("repCode") or "0000") != "0000":
        logger.warning("获取验证码失败: repCode=%s", inner.get("repCode"))
        return None
    rep = inner.get("repData") or {}
    if not rep.get("secretKey") or not rep.get("originalImageBase64") or not rep.get("token"):
        logger.warning("验证码返回字段缺失: %s", list(rep))
        return None
    return rep


def decode_captcha_image(rep: Dict[str, Any]) -> bytes:
    return base64.b64decode(rep["originalImageBase64"])


def check_captcha(client, rep: Dict[str, Any], points: List[Tuple[int, int]]) -> Optional[Dict[str, Any]]:
    point_json = build_point_json(rep["secretKey"], points)
    status, body = client.post_form("/api/captcha/check", {
        "captchaType": CAPTCHA_TYPE,
        "pointJson": point_json,
        "token": rep["token"],
    })
    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError:
        logger.warning("验证码校验返回非 JSON: %s", (body or "")[:200])
        return None
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    rep_data = inner.get("repData") or {}
    if not rep_data.get("result"):
        logger.warning("验证码校验未通过: %s", (body or "")[:200])
        return None
    points_json = json.dumps([{"x": int(x), "y": int(y)} for x, y in points], separators=(",", ":"))
    resp_points_json = None
    try:
        raw = AES.new(rep["secretKey"].encode("utf-8"), AES.MODE_ECB).decrypt(
            base64.b64decode(rep_data.get("pointJson") or "")
        )
        decoded = raw[:-raw[-1]].decode("utf-8")
        if decoded.startswith("["):
            resp_points_json = decoded
    except Exception:  # noqa: BLE001
        resp_points_json = None
    logger.info("响应里的坐标：%s", resp_points_json)
    return {
        "point_json": point_json,
        "response": rep_data,
        # 三种可能的拼法，按优先级尝试（服务端只认其中一种）
        "variants": [
            build_captcha_verification(rep["secretKey"], rep["token"], point_json),
            build_captcha_verification(rep["secretKey"], rep["token"], rep_data.get("pointJson") or point_json),
            build_captcha_verification(rep["secretKey"], rep["token"], points_json),
            build_captcha_verification(rep["secretKey"], rep_data.get("token") or rep["token"],
                                       rep_data.get("pointJson") or point_json),
            build_captcha_verification(rep["secretKey"], rep["token"], resp_points_json or points_json),
            build_captcha_verification(rep["secretKey"], rep_data.get("token") or rep["token"], point_json),
        ],
        "verification": build_captcha_verification(rep["secretKey"], rep["token"], point_json),
    }


def submit_order(client, payload: Dict[str, Any]) -> Tuple[int, str]:
    return client.post_form("/api/reservation/order/submit", payload)
