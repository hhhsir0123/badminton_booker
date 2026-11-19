"""命令行入口。

    python main.py --web          启动网页控制台（推荐）
    python main.py --check        检查配置并打印下一次的计划
    python main.py --peek         立即查询目标日期当前可订场次（不下单）
    python main.py                按配置抢一次（自带等待到开售）
    python main.py --poll         空场轮询
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from src.config import Config, validate_config  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402

logger = setup_logger("main", log_file=os.path.join(ROOT, "logs", "main.log"))


def resolve_config(path: str | None) -> str:
    """找到配置文件；没有就写一份默认的，避免第一次运行直接报错。"""
    target = path or os.environ.get("BB_CONFIG") or os.path.join(ROOT, "config.yaml")
    config = Config(target)
    if not os.path.exists(target):
        config.save()
        logger.info("已生成默认配置: %s", target)
    return target


def check(config_path: str) -> int:
    from src.pipeline.ticket_grabbing_pipeline import booking_plan, preferences

    config = Config(config_path)
    errors = validate_config(config.config)
    if errors:
        for error in errors:
            logger.error("配置错误: %s", error)
        return 1

    opening, play_day = booking_plan(config, datetime.now(timezone(timedelta(hours=8))))
    logger.info("配置文件: %s", config_path)
    logger.info("下一次开售: %s（入场日 %s）", opening.isoformat(" "), play_day)
    for index, (court, span, grouped) in enumerate(preferences(config), 1):
        target = court or "任意场地"
        window = f"{span[0]}-{span[1]}" if span else "、".join(f"{a}-{b}" for a, b in grouped)
        logger.info("  候选 %s: %s %s", index, target, window)
    return 0


def run_once(config_path: str, preview: bool = False, dry_run: bool = False,
             overrides: dict | None = None) -> int:
    from src.pipeline.ticket_grabbing_pipeline import TicketGrabbingPipeline

    pipeline = TicketGrabbingPipeline(config_path, overrides=overrides)
    try:
        ok = pipeline.run(preview=preview, dry_run=dry_run)
    finally:
        pipeline.cleanup()
    return 0 if ok else 2


def run_poll(config_path: str) -> int:
    from src.pipeline.periodic_scanning_pipeline import PeriodicScanningPipeline

    pipeline = PeriodicScanningPipeline(config_path)
    try:
        ok = pipeline.run()
    finally:
        pipeline.cleanup()
    return 0 if ok else 2


def open_config_page(config_path: str) -> int:
    """在浏览器里打开纯静态的配置编辑页（不上任何服务）。"""
    import webbrowser
    from pathlib import Path

    page = Path(ROOT) / "src" / "web" / "index.html"
    if not page.exists():
        logger.error("找不到配置页面: %s", page)
        return 1
    webbrowser.open(page.as_uri())
    logger.info("已打开配置页面: %s", page)
    logger.info("编辑后点「导出 config.yaml」，覆盖 %s 即可", config_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="羽毛球场地自动预订")
    parser.add_argument("--config", default=None, help="配置文件路径，默认 ./config.yaml")
    parser.add_argument("--web", action="store_true", help="打开静态配置编辑页")
    parser.add_argument("--check", action="store_true", help="检查配置并打印计划")
    parser.add_argument("--peek", action="store_true", help="立即查询可订场次，不下单")
    parser.add_argument("--dry-run", action="store_true", help="演练：真的选场，但不提交订单")
    parser.add_argument("--date", default=None, help="临时指定入场日 YYYY-MM-DD（不改配置文件）")
    parser.add_argument("--poll", action="store_true", help="空场轮询")
    args = parser.parse_args()

    config_path = resolve_config(args.config)
    overrides = {"booking.booking_date": args.date} if args.date else None

    if args.web:
        return open_config_page(config_path)
    if args.check:
        return check(config_path)
    if args.dry_run:
        return run_once(config_path, dry_run=True, overrides=overrides)
    if args.peek:
        return run_once(config_path, preview=True, overrides=overrides)
    if args.poll:
        return run_poll(config_path)
    return run_once(config_path, overrides=overrides)


if __name__ == "__main__":
    sys.exit(main())
