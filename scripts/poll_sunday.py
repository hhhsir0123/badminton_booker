"""单独起一个轮询进程（周日常用），不影响控制台里正在跑的轮询。

用法：
    .venv/bin/python -u scripts/poll_sunday.py 2026-11-29 14:00-21:00 2
"""
from __future__ import annotations

import os
import sys

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline  # noqa: E402


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-11-29"
    span = sys.argv[2] if len(sys.argv) > 2 else "14:00-21:00"
    hours = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    with open(os.path.join(PROJECT_ROOT, "config.yaml"), encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    polling = data.setdefault("polling", {})
    polling.update({
        "polling_dates": [day],
        "polling_time_range": span,
        "polling_hours": hours,
        "polling_courts": ["全部场地"],
        "is_auto_booking": True,
        "polling_interval": 60,
        "heartbeat_seconds": 3600,
    })
    # 独立 profile：不和主控制台抢同一个 Chrome 用户目录
    login = data.setdefault("login", {})
    login["profile_dir"] = os.path.join(PROJECT_ROOT, ".chrome-profile-sunday")

    path = os.path.join(PROJECT_ROOT, "logs", "poll-sunday.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    print(f"[poll-sunday] 日期={day} 时段={span} 连续={hours}h profile={login['profile_dir']}", flush=True)
    pipeline = PeriodicScanningPipeline(path)
    return 0 if pipeline.run() else 1


if __name__ == "__main__":
    raise SystemExit(main())
