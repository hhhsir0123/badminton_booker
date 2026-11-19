"""
配置管理模块

设计要点：
1. 代码内置 DEFAULTS，config.yaml 只需要写想改的字段，旧配置文件不会因为新增字段而报错。
2. validate() 返回全部错误而不是抛第一个，Web 页面可以直接展示。
3. save() 使用「临时文件 + os.replace」原子写入，避免 Web 保存过程中进程被杀导致配置损坏。
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
from typing import Any, Dict, List

import yaml

TIME_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TIME_CLOCK_SECONDS = re.compile(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$")


DEFAULTS: Dict[str, Any] = {
    "user": {
        "username": "",
        "password": "",
        "partner_name": "",
    },
    "booking": {
        "venue_type": "羽毛球",
        # 平台每天放场的时间点（北京时间）
        "target_time_in_day": "07:00:00",
        # 提前几天入场：2 表示今天 7 点抢后天的场
        "booking_days_ahead": 2,
        # 指定入场日（YYYY-MM-DD）；留空则按 booking_days_ahead 推算
        "booking_date": "",
        # 提前多少秒打开浏览器、恢复会话
        "advance_time": 120,
        # 优先级从高到低，字段：court / start / end
        "preferences": [
            {"court": "任意", "start": "19:00", "end": "21:00"},
        ],
        # 候选时段只抢到一部分时是否仍然提交
        "allow_partial": False,
        # 所有候选都不满足时，退而求其次抢「当天任意最早的一段」
        "fallback_any": False,
        # 一次放场最多提交几次（防止脚本化重试触发风控）
        "max_attempts": 3,
    },
    "login": {
        "url": "https://ggtypt.cumtb.edu.cn/venue/login",
        "headless": False,
        "auto_captcha": True,
        "max_retry": 2,
        # 持久化 Chrome 配置目录：复用 Cookie，07:00 无需重新登录/过验证码
        "profile_dir": ".chrome-profile",
        "session_check": True,
    },
    "polling": {
        "polling_interval": 60,
        "polling_time_slots": [],
        "polling_date_offsets": [0, 1, 2],
        "is_auto_booking": False,
    },
    "feishu": {
        # 群机器人 Incoming Webhook；留空则关闭
        "webhook": "",
        # 机器人开启「签名校验」时填这里
        "secret": "",
        "mention_all": False,
        # 轮询发现空场时推送
        "notify_availability": True,
        # 抢到时推送
        "notify_success": True,
        # 没抢到时也推送（每天一条，嫌吵就关掉）
        "notify_failure": False,
    },
    "captcha": {
        # 点选验证码的识别来源：local（本地 OCR）或 ttshitu（图鉴打码平台）
        "provider": "local",
        # 平台失败时是否回落到本地 OCR
        "fallback_to_local": True,
        "ttshitu": {
            # 注册后填这里，或用环境变量 TTSHITU_USER / TTSHITU_PASS
            "username": "",
            "password": "",
            # 20 = 点选 3 个坐标（AJ-Captcha 点选正好 3 个字）
            "type_id": 20,
            "timeout_seconds": 8.0,
        },
        # 一次验证码最多尝试几次（AJ-Captcha 失败会锁 get 接口，建议 1）
        "max_attempts": 1,
        # 识别结果的长度范围
        "min_length": 4,
        "max_length": 6,
        # 多种预处理投票（更准，CPU 多花几十毫秒）
        "ensemble": True,
        # 提前到开售前处理验证码（能提前解决就省掉 07:00 的一次交互）
        "pre_solve": True,
        # 检测到滑块验证码时，留多少秒给人工拖动（0 = 不等待，直接放弃提交）
        "slider_wait_seconds": 0,
    },
    "runtime": {
        # 手工时钟校准（毫秒）；0 表示使用 HTTP Date 自动估算
        "clock_offset_ms": 0,
        # 放场探测：提前多久开始探测、探测间隔、探测持续多久
        "release_probe_lead_ms": 1500,
        # day/info 一次返回约 90KB，间隔太密就是给自己找封号
        "release_probe_interval_ms": 200,
        "release_grace_ms": 4000,
        # auto / click / reload —— 抢场时刷新预约表的方式
        "grid_refresh": "auto",
        # 提交后等待平台回执的最长时间
        "submit_timeout_ms": 6000,
        # 抢到之后浏览器保持打开的秒数（订单要在同一个窗口里付款）
        "keep_open_after_success": 600,
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并，override 优先。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _get(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    value: Any = data
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _flatten(data: Any, prefix: str = "") -> set:
    """把嵌套字典展开成点号键集合，用于判断「用户显式写了哪些字段」。"""
    keys = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.add(path)
            keys.update(_flatten(value, path))
    return keys


def validate_config(data: Dict[str, Any]) -> List[str]:
    """返回全部配置错误的可读列表。"""
    errors: List[str] = []

    release = _get(data, "booking.target_time_in_day", "")
    if not isinstance(release, str) or not TIME_CLOCK_SECONDS.match(release.strip()):
        errors.append("开售时间需要 HH:MM:SS 格式，例如 07:00:00")

    ahead = _get(data, "booking.booking_days_ahead")
    if not _is_int(ahead) or not 0 <= ahead <= 30:
        errors.append("提前天数应为 0~30 的整数")

    advance = _get(data, "booking.advance_time")
    if not _is_int(advance) or not 10 <= advance <= 3600:
        errors.append("提前打开时间应为 10~3600 秒")

    attempts = _get(data, "booking.max_attempts")
    if not _is_int(attempts) or not 1 <= attempts <= 10:
        errors.append("最大提交次数应为 1~10")

    fixed = _get(data, "booking.booking_date", "")
    if fixed not in ("", None):
        if not isinstance(fixed, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", fixed):
            errors.append("指定入场日需要 YYYY-MM-DD 格式")

    items = _get(data, "booking.preferences")
    if not isinstance(items, list) or not items:
        errors.append("至少需要一条场地/时段偏好")
    else:
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                errors.append(f"第 {index} 条偏好格式错误")
                continue
            if not isinstance(item.get("court"), str) or not item["court"].strip():
                errors.append(f"第 {index} 条偏好缺少场地")
            start, end = item.get("start"), item.get("end")
            if not isinstance(start, str) or not TIME_CLOCK.match(start):
                errors.append(f"第 {index} 条偏好的开始时间需要 HH:MM")
            if not isinstance(end, str) or not TIME_CLOCK.match(end):
                errors.append(f"第 {index} 条偏好的结束时间需要 HH:MM")
            if isinstance(start, str) and TIME_CLOCK.match(start) and not start.endswith(":00"):
                errors.append(f"第 {index} 条偏好的开始时间只支持整点（HH:00）")
            if isinstance(end, str) and TIME_CLOCK.match(end) and not end.endswith(":00"):
                errors.append(f"第 {index} 条偏好的结束时间只支持整点（HH:00）")
            if isinstance(start, str) and isinstance(end, str) and TIME_CLOCK.match(start) and TIME_CLOCK.match(end):
                if start >= end:
                    errors.append(f"第 {index} 条偏好的结束时间要晚于开始时间")

    url = _get(data, "login.url", "")
    if not isinstance(url, str) or not url.startswith("http"):
        errors.append("登录地址需要以 http(s):// 开头")

    interval = _get(data, "polling.polling_interval")
    if not _is_int(interval) or not 5 <= interval <= 3600:
        errors.append("轮询间隔应为 5~3600 秒")

    offsets = _get(data, "polling.polling_date_offsets")
    if not isinstance(offsets, list) or not offsets:
        errors.append("轮询日期偏移需要至少一个数字")
    else:
        for offset in offsets:
            if not _is_int(offset) or not 0 <= offset <= 30:
                errors.append("轮询日期偏移应为 0~30 的整数")
                break

    webhook = _get(data, "feishu.webhook", "")
    if webhook not in ("", None):
        if not isinstance(webhook, str) or not webhook.startswith("http"):
            errors.append("飞书 webhook 需要以 https:// 开头（open-apis/bot/v2/hook/…）")

    secret = _get(data, "feishu.secret", "")
    if secret not in ("", None) and not isinstance(secret, str):
        errors.append("飞书 secret 应为字符串")

    return errors


PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
NAME_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z·\s]{2,20}$")


def validate_for_run(data: Dict[str, Any]) -> List[str]:
    """在 validate_config 之上，校验「真正能跑起来」需要的字段。

    用于保存配置和启动任务：账号、同伴、验证码凭据缺失或格式错误要立刻报出来。
    """
    errors = list(validate_config(data))

    username = str(_get(data, "user.username", "") or "").strip()
    if not username:
        errors.append("请填写登录手机号（系统设置 → 账号）")
    elif not PHONE_RE.match(username):
        errors.append("登录手机号应为 11 位手机号")

    if not str(_get(data, "user.password", "") or ""):
        errors.append("请填写登录密码（系统设置 → 账号）")

    partner = str(_get(data, "user.partner_name", "") or "").strip()
    if not partner:
        errors.append("请填写默认同伴（系统设置 → 账号）")
    elif not NAME_RE.match(partner):
        errors.append("默认同伴姓名不合法（2~20 个中文/字母）")

    provider = str(_get(data, "captcha.provider", "local") or "local").lower()
    if provider not in ("local", "ttshitu"):
        errors.append("验证码识别方式只能是 local 或 ttshitu")
    elif provider == "ttshitu":
        tts_user = str(_get(data, "captcha.ttshitu.username", "") or "").strip()
        tts_pass = str(_get(data, "captcha.ttshitu.password", "") or "")
        if not tts_user:
            errors.append("选择图鉴打码时需要填写图鉴用户名")
        if not tts_pass:
            errors.append("选择图鉴打码时需要填写图鉴密码")
        type_id = _get(data, "captcha.ttshitu.type_id", 43)
        if not _is_int(type_id) or not 1 <= type_id <= 99:
            errors.append("图鉴题型应为 1~99 的整数（推荐 43）")

    return errors


class Config:
    """配置管理类"""

    def __init__(self, config_path: str = "config.yaml", *, allow_missing: bool = True):
        self.config_path = config_path
        self.explicit: set = set()
        self.config: Dict[str, Any] = self._load_config(allow_missing)

    def _load_config(self, allow_missing: bool) -> Dict[str, Any]:
        if not os.path.exists(self.config_path):
            if not allow_missing:
                raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
            return copy.deepcopy(DEFAULTS)

        with open(self.config_path, "r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"配置文件格式错误，顶层应为字典: {self.config_path}")
        self.explicit = _flatten(loaded)
        return deep_merge(DEFAULTS, loaded)

    def has_explicit(self, key: str) -> bool:
        """用户是否在 config.yaml 里显式写过这个键（区分默认值和显式配置）。"""
        return key in self.explicit

    def get(self, key: str, default: Any = None) -> Any:
        """支持点号分隔的多级键，例如 'user.username'。"""
        value = _get(self.config, key, None)
        return default if value is None else value

    def set(self, key: str, value: Any):
        keys = key.split(".")
        node = self.config
        for part in keys[:-1]:
            node = node.setdefault(part, {})
        node[keys[-1]] = value

    def update(self, payload: Dict[str, Any]):
        """把 Web 提交的部分配置合并进来（并保留未提交的字段）。"""
        self.config = deep_merge(self.config, payload)

    def validate(self):
        errors = validate_config(self.config)
        if errors:
            raise ValueError("；".join(errors))

    def dumps(self) -> str:
        return yaml.safe_dump(self.config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def save(self):
        """原子写入：先写同目录临时文件，再 os.replace。"""
        self.validate()
        directory = os.path.dirname(os.path.abspath(self.config_path)) or "."
        os.makedirs(directory, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=".config-", suffix=".yaml", delete=False
        )
        try:
            with handle:
                handle.write(self.dumps())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.config_path)
        finally:
            if os.path.exists(handle.name):
                os.unlink(handle.name)
