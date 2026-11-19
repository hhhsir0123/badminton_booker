# 图鉴（ttshitu）打码平台接入指南

本项目的下单验证码是 AJ-Captcha「点选文字」（clickWord）：一张图里有 4 个汉字，
其中 3 个是目标字，需要**按顺序点击**。本地 OCR 在旋转字形上准确率不够稳定，
可以接图鉴打码平台（`typeid=20` 点选 3 个坐标）把准确率拉到 90% 以上。

---

## 一、注册

1. 打开 <https://ttshitu.com/>，点右上角「注册」。
2. 用手机号注册（收短信验证码），设置**登录密码**。
3. 注册后进入「用户中心」：
   - 「用户名」就是 API 用的 `username`（一般是手机号或自定义账号）；
   - 「登录密码」就是 API 用的 `password`；
   - 建议顺手绑定邮箱，方便找回。

> 注意：API 用的是**账号 + 登录密码**，不是短信验证码；密码改过之后记得同步改配置。

## 二、充值

1. 用户中心 →「充值」→ 选金额（支持支付宝/微信）。
2. 最低 1 元起，点选类题号一般 **0.01~0.02 元/次**（以充值页实时价格为准）。
3. 充 10 元大约能测 500~1000 次，足够先验证效果。
4. 充值后在「消费记录」能看到每次调用的扣费，便于核对。

## 三、题号（typeid）

| 题号 | 含义 | 本场景 |
| --- | --- | --- |
| 19 | 点选 1 个坐标 | |
| **43** | **点选（传标题文本 content）** | ✅ 用这个（闭集匹配） |
| 20 | 点选 3 个坐标（不传词表） | ⚠️ 平台自己猜字，实测会挑中干扰字 |
| 21 | 点选 3~5 个坐标 | |
| 22 | 点选 5~8 个坐标 | |

## 四、配置

编辑 `config.yaml`：

```yaml
captcha:
  provider: ttshitu        # local = 只用本地 OCR；ttshitu = 优先打码平台
  fallback_to_local: true  # 平台失败时回落本地 OCR
  max_attempts: 1          # AJ-Captcha 失败会锁 get 接口，别重试
  ttshitu:
    username: "你的用户名"
    password: "你的密码"
    type_id: 43
    timeout_seconds: 8.0
```

也可以不改配置文件，用环境变量：

```powershell
$env:TTSHITU_USER="你的用户名"
$env:TTSHITU_PASS="你的密码"
```

## 五、测试

```powershell
# 1) 干跑：只检查参数拼装，不发请求、不扣费
python test_files/demo_ttshitu.py --image captcha_images/submit_samples/submit_0.png --dry-run

# 2) 真实调用一次（约 0.01 元）
python test_files/demo_ttshitu.py --image captcha_images/submit_samples/submit_0.png

# 3) 和本地 OCR 对比坐标（距离越小说明两边一致）
python test_files/demo_ttshitu.py --image captcha_images/submit_samples/submit_0.png --compare
```

批量测准确率（20 张真实样本）：

```powershell
Get-ChildItem captcha_images/submit_samples/*.png | ForEach-Object {
    python test_files/demo_ttshitu.py --image $_.FullName
}
```

如果 20 张里有 18 张以上返回合理坐标，就可以接进抢票流程；否则先别用。

## 六、接入抢票流程

```python
from src.config import Config
from src.utils.click_word_solver import ClickWordSolver
from src.utils.ttshitu_provider import TTShituProvider

config = Config("config.yaml")
provider = TTShituProvider.from_config(config) if config.get("captcha.provider") == "ttshitu" else None

solver = ClickWordSolver(provider=provider)
points = solver.solve(captcha_png, words)      # 3 个 (x, y)，310×155 坐标系
# 点击：canvasClick({offsetX: x * imgWidth / 310, offsetY: y * imgHeight / 155})
```

## 七、费用与延迟

| 项目 | 数值 |
| --- | --- |
| 单次费用 | 约 0.01~0.02 元 |
| 一次抢票 | 1~2 次调用 = 2~4 分钱 |
| 每天一次 | 一个月约 1 元 |
| 延迟 | 1~3 秒（网络波动时更久） |

## 八、注意事项

1. **不要在 07:00 才调用**：1~3 秒的延迟可能让场次被别人抢走。验证码的
   `captchaVerification` 有效期是 3 分钟（源码常量 `EXPIRESIN_THREE`），
   应该在 06:59 左右预解，07:00 只做「选场 + 提交」。
2. **失败不要刷新**：AJ-Captcha 内置了按分钟的失败计数和 get 接口锁定
   （`REQ_GET_LOCK_SECONDS`），刷多了会直接拿不到验证码。
3. **只上传验证码图片**：不要夹带账号、cookie、订单信息。
4. **平台可能调整题号或价格**：上线前用 `--dry-run` 和 20 张样本复测一次。
