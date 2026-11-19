"""图鉴打码平台演示脚本（识别点选验证码，返回点击坐标）。

用法：
    # 1) 先干跑，确认参数拼装正确（不发网络请求、不扣费）
    python test_files/demo_ttshitu.py --image captcha_images/submit_samples/submit_0.png --dry-run

    # 2) 真实调用（会产生费用，约 0.01 元/次）
    python test_files/demo_ttshitu.py --image captcha_images/submit_samples/submit_0.png

    # 3) 和本地 OCR 对比坐标
    python test_files/demo_ttshitu.py --image ... --compare

账号来源（优先级从高到低）：
    1) 环境变量 TTSHITU_USER / TTSHITU_PASS
    2) config.yaml 里的 captcha.ttshitu.username / password
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config  # noqa: E402
from src.utils.ttshitu_provider import TTShituError, TTShituProvider  # noqa: E402


def load_credentials(config_path):
    username = os.environ.get("TTSHITU_USER", "")
    password = os.environ.get("TTSHITU_PASS", "")
    if username and password:
        return username, password, 20
    config = Config(config_path)
    return (
        config.get("captcha.ttshitu.username", ""),
        config.get("captcha.ttshitu.password", ""),
        config.get("captcha.ttshitu.type_id", 20),
    )


def main():
    parser = argparse.ArgumentParser(description="图鉴点选验证码识别演示")
    parser.add_argument("--image", required=True, help="验证码图片路径")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--type-id", type=int, default=None, help="题号，默认 20（点选 3 个坐标）")
    parser.add_argument("--dry-run", action="store_true", help="只打印请求参数，不发请求")
    parser.add_argument("--compare", action="store_true", help="同时跑本地 OCR 对比坐标")
    args = parser.parse_args()

    with open(args.image, "rb") as handle:
        image_bytes = handle.read()

    username, password, type_id = load_credentials(args.config)
    provider = TTShituProvider(username, password, type_id=args.type_id or type_id)

    if args.dry_run:
        payload = provider.payload(image_bytes, args.type_id)
        print("[dry-run] 接口:", "http://api.ttshitu.com/predict")
        print("[dry-run] username:", payload["username"] or "(空，需要配置)")
        print("[dry-run] typeid:", payload["typeid"])
        print("[dry-run] image base64 长度:", len(payload["image"]), "图片字节:", len(image_bytes))
        return

    try:
        points = provider.solve(image_bytes, args.type_id)
    except TTShituError as error:
        print("识别失败:", error, "(code=%s)" % getattr(error, "code", None))
        return
    print("图鉴返回坐标:", points)

    if args.compare:
        try:
            from src.utils.click_word_solver import ClickWordSolver

            solver = ClickWordSolver()
            meta_path = os.path.splitext(args.image)[0] + ".json"
            words = []
            if os.path.exists(meta_path):
                import json

                words = json.load(open(meta_path, encoding="utf-8")).get("words") or []
            if not words:
                print("没有 .json 词表，跳过本地对比")
                return
            local = solver.solve(image_bytes, words)
            print("本地 OCR 坐标:", local)
            if local:
                for index, (a, b) in enumerate(zip(points, local)):
                    print(f"  #{index}: 图鉴{a} 本地{b} 距离 {abs(a[0]-b[0])+abs(a[1]-b[1])}px")
        except Exception as error:  # noqa: BLE001
            print("本地对比失败:", error)


if __name__ == "__main__":
    main()
