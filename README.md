# 羽毛球场地自动预订

每天 07:00 放场时，自动抢**两天后**的羽毛球场。附一个**纯静态**配置页面，
在浏览器里改参数、导出 `config.yaml` 即可，不需要起任何服务。

> 只用于自己的校园账号，请遵守学校的场馆预约规则。

---

## 一、5 分钟上手

### 1. 装依赖

需要 **Python 3.9+** 和 **Chrome**。

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

### 2. 生成配置

```bash
copy config.example.yaml config.yaml     # Windows
cp config.example.yaml config.yaml       # macOS / Linux
```

也可以直接打开配置页（推荐）：

```bash
python main.py --web
```

### 3. 填最少的两项

打开 `config.yaml`，只改这两项就能跑：

```yaml
user:
  username: "你的校园账号手机号"
  partner_name: "默认同伴姓名"     # 可选，平台里已加过的人
booking:
  preferences:
    - court: 任意
      start: 19:00
      end: 21:00
```

其余字段都有默认值。想可视化改，用 `python main.py --web`。

### 4. 先看计划，再试运行

```bash
python main.py --check     # 下一次开售时间、入场日、候选
python main.py --peek      # 现在目标日期还能订什么（不下单）
python main.py --dry-run   # 真的点场格，但不提交订单
```

三步都正常，再正式跑：

```bash
python main.py             # 等到 07:00，抢一次
```

---

## 二、配置

### 2.1 配置页面（推荐）

```bash
python main.py --web
```

页面分三块：

| 区域 | 内容 |
| --- | --- |
| **账号** | 登录手机号、默认同伴（一次填好，以后不用再填） |
| **场地与时段** | 从上到下依次尝试的候选，可增删排序 |
| **验证码** | 本地识别（免费）或图鉴打码（更准，约 1 分钱/次） |

开售时间、提前准备、提前几天入场、飞书通知、轮询参数都收在**高级设置**里，平时不用管。

流程：**载入 `config.yaml` → 改 → 导出 → 覆盖项目根目录的原文件**。
草稿存在浏览器 localStorage，刷新不丢；`Ctrl+S` 直接导出。

### 2.2 配置文件字段

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `user.username` | — | 登录手机号 |
| `user.partner_name` | 空 | 默认同伴，留空表示不指定 |
| `booking.target_time_in_day` | `07:00:00` | 平台放场时间 |
| `booking.booking_days_ahead` | `2` | 抢几天后的场地 |
| `booking.advance_time` | `120` | 提前多少秒开始准备（登录、开浏览器） |
| `booking.preferences` | 2 条 | 候选场地/时段，按顺序尝试 |
| `booking.max_attempts` | `3` | 最多提交几次 |
| `booking.allow_partial` | `false` | 只抢到部分时段也提交 |
| `booking.fallback_any` | `false` | 候选都不满足时抢任意空场 |
| `captcha.provider` | `local` | `local` 本地识别 / `ttshitu` 图鉴 |
| `captcha.ttshitu.*` | 空 | 图鉴账号（选图鉴时必填） |
| `feishu.webhook` | 空 | 飞书群机器人，填了就推送结果 |
| `runtime.keep_open_after_success` | `600` | 抢到后浏览器保持打开，用于付款 |

完整字段见 [`config.example.yaml`](config.example.yaml)。

---

## 三、启动方式

| 命令 | 作用 | 会下单吗 |
| --- | --- | --- |
| `python main.py --check` | 校验配置、打印下一次计划 | 否 |
| `python main.py --peek` | 查一次目标日期可订场次 | 否 |
| `python main.py --dry-run` | 演练选场全流程 | 否 |
| `python main.py --date 2026-11-21` | 临时指定入场日 | 否（配合 `--peek`） |
| `python main.py` | 等到开售，抢一次 | **是** |
| `python main.py --poll` | 空场轮询，发现空场就通知 | 看配置 |
| `python main.py --web` | 打开配置页面 | 否 |

日志在 `logs/`，运行结果也会写进 `logs/main.log`。

---

## 四、部署：每天自动跑

### Windows（任务计划程序）

1. 新建任务 → 触发器：每天 **06:58**；
2. 操作：启动程序
   - 程序：`C:\path\to\project\.venv\Scripts\python.exe`
   - 参数：`main.py`
   - 起始于：`C:\path\to\project`
3. 勾选「不管用户是否登录都要运行」需要配置密码，否则选「只在用户登录时运行」。

### macOS（launchd）

`~/Library/LaunchAgents/com.badminton.booker.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.badminton.booker</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/你的用户名/badminton_booker/.venv/bin/python</string>
    <string>/Users/你的用户名/badminton_booker/main.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/你的用户名/badminton_booker</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>58</integer></dict>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.badminton.booker.plist
```

macOS 休眠时不会执行，抢票当天要保证机器唤醒（`caffeinate -i python main.py`）。

### Linux（cron）

```bash
crontab -e
58 6 * * * cd /path/to/project && .venv/bin/python main.py
```

### 登录状态

程序用持久化 profile（默认 `.chrome-profile/`），**第一次手动登录一次**之后，
后面都复用这个会话，不用每天扫码/输密码。

---

## 五、macOS 额外要做的

```bash
brew install --cask google-chrome
brew install --cask chromedriver
xattr -d com.apple.quarantine "$(which chromedriver)"   # 绕过 Gatekeeper
python main.py --check
python test_files/e2e_local.py        # 不需要登录，验证 Selenium 链路
```

如果 `ddddocr` 导入报错：`pip install onnxruntime`。

---

## 六、常见问题

| 现象 | 处理 |
| --- | --- |
| 提示需要登录 | 先用 `--web` 或手动打开一次登录，profile 会记住 |
| 浏览器启动失败 | Chrome 与 chromedriver 版本要一致；把驱动放到 `drivers/` |
| 验证码总是失败 | 改用图鉴：`captcha.provider: ttshitu`（见 [docs/ttshitu.md](docs/ttshitu.md)） |
| 提交报「存在未支付订单」 | 先去平台取消未支付订单 |
| 没抢到 | 看 `logs/main.log`，确认是"没读到场次"还是"提交被抢" |

---

## 七、更多文档

| 文档 | 内容 |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | 架构图、依赖、抢票/轮询链路、加速与成功率保障 |
| [docs/captcha-solution.md](docs/captcha-solution.md) | 验证码踩坑记录与预解方案 |
| [docs/ttshitu.md](docs/ttshitu.md) | 图鉴打码注册、充值与接入 |

---

## 八、说明

- 程序只在**放场前探测一次数据量**、放场后读表，不会高频刷接口；
- 验证码**只解一次**，失败不反复刷新（平台有失败锁）；
- 抢到后浏览器会保持打开，方便你及时付款。
