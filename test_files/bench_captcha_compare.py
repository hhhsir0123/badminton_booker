"""对比本地 OCR 与图鉴打码平台在真实验证码上的成功率。

用法：
    python test_files/bench_captcha_compare.py --samples captcha_images/submit_samples

没有图鉴账号时只跑本地 OCR。
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config  # noqa: E402
from src.utils.click_word_solver import ClickWordSolver  # noqa: E402
from src.utils.ttshitu_provider import TTShituError, TTShituProvider  # noqa: E402


def load_samples(folder):
    samples = []
    for path in sorted(glob.glob(os.path.join(folder, "*.png"))):
        meta_path = os.path.splitext(path)[0] + ".json"
        if not os.path.exists(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as handle:
            words = (json.load(handle) or {}).get("words") or []
        if len(words) >= 3:
            samples.append({"path": path, "words": words})
    return samples


def run_local(samples, solver):
    rows = []
    for sample in samples:
        with open(sample["path"], "rb") as handle:
            png = handle.read()
        started = time.monotonic()
        points = solver.solve(png, sample["words"])
        rows.append({"file": os.path.basename(sample["path"]), "ok": bool(points),
                     "ms": int((time.monotonic() - started) * 1000)})
    return rows


def run_ttshitu(samples, provider):
    rows = []
    for sample in samples:
        with open(sample["path"], "rb") as handle:
            png = handle.read()
        started = time.monotonic()
        ok, error = True, None
        try:
            points = provider.solve(png)
            ok = len(points) == len(sample["words"])
        except TTShituError as exc:
            ok, error = False, str(exc)
        rows.append({"file": os.path.basename(sample["path"]), "ok": ok,
                     "ms": int((time.monotonic() - started) * 1000), "error": error})
    return rows


def summarize(name, rows):
    if not rows:
        print(f"{name}: 没有样本")
        return
    ok = sum(1 for r in rows if r["ok"])
    print(f"{name}: {ok}/{len(rows)} = {ok/len(rows):.0%}, 中位 {statistics.median([r['ms'] for r in rows]):.0f}ms")
    for r in rows:
        flag = "OK " if r["ok"] else "FAIL"
        print(f"  {flag} {r['file']} {r['ms']}ms" + (f" {r.get('error')}" if r.get("error") else ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", default="captcha_images/submit_samples")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    samples = load_samples(args.samples)
    print(f"样本 {len(samples)} 张：{args.samples}")
    if not samples:
        return

    solver = ClickWordSolver()
    solver.warmup()
    summary = {"local": run_local(samples, solver)}
    summarize("本地 OCR", summary["local"])

    config = Config(args.config)
    if str(config.get("captcha.provider", "local")).lower() == "ttshitu":
        try:
            provider = TTShituProvider.from_config(config)
            summary["ttshitu"] = run_ttshitu(samples, provider)
            summarize("图鉴平台", summary["ttshitu"])
        except TTShituError as error:
            print("图鉴未配置或不可用:", error)
    else:
        print("config.yaml 里 captcha.provider 不是 ttshitu，跳过平台对比")

    local_ok = sum(1 for r in summary["local"] if r["ok"])
    out = {"samples": len(samples), "local_ok": local_ok,
           "local_rate": local_ok / len(samples), "rows": summary}
    path = os.path.join(args.samples, "compare_result.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, ensure_ascii=False, indent=2)
    print("结果已写入", path)


if __name__ == "__main__":
    main()
