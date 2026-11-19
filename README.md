# 羽毛球场地自动预订

每天 07:00 放场时，自动抢**两天后**的羽毛球场。
配置、启动、停止都在一个本地页面里完成——**点按钮就行，不用敲命令**。

> 只用于自己的校园账号，请遵守学校的场馆预约规则。

---

## 一、第一次使用（只做一次）

### 1. 装 Python 和 Chrome

- Python 3.9+：<https://www.python.org/downloads/>（安装时勾选 "Add Python to PATH"）
- Chrome：<https://www.google.com/chrome/>

### 2. 装依赖

在项目目录里打开终端，执行一次：

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

### 3. 之后都双击启动

| 系统 | 双击这个文件 |
| --- | --- |
| Windows | **`启动控制台.bat`** |
| macOS / Linux | **`start-console.command`**（第一次可能要在「系统设置 → 隐私与安全性」里允许） |

浏览器会自动打开 <http://127.0.0.1:8765/>，这就是控制台。

> macOS 首次运行如果提示没有执行权限：在终端执行 `chmod +x start-console.command`。

---

## 二、页面怎么用

页面右侧是「运行控制」，左侧是配置。流程只有三步：

```
① 填「账号」→ 登录手机号、默认同伴
② 点「保存配置到程序」
③ 点「开始抢票」
```

- 程序会**等到开售前 2 分钟**才开始准备（开浏览器、恢复登录、预解验证码），
  这段时间页面显示「等待 07:00 开售」，可以放着不管；
- 想提前结束，点「停止」；
- **抢票期间不要关闭那个黑色命令行窗口**，关了程序就退出了；
- 页面下方的日志区会实时显示进度。

### 页面上的三块配置

| 区域 | 填什么 |
| --- | --- |
| **账号** | 登录手机号；默认同伴（平台里已加过的人，可留空） |
| **场地与时段** | 候选顺序，从上到下依次尝试；场地是下拉框 |
| **验证码** | 默认「本地识别（免费）」；想更稳选「图鉴打码」（需要图鉴账号，见 [docs/ttshitu.md](docs/ttshitu.md)） |

**高级设置**（折叠着，平时不用管）：开售时间、提前准备秒数、提前几天入场、最多提交次数、
放场探测间隔、抢到后浏览器保留时长、空场轮询、飞书通知。

### 按钮说明

| 按钮 | 作用 |
| --- | --- |
| 开始抢票 | 等到开售，抢 D+N 的场地（会真下单） |
| 开始轮询 | 按间隔扫空场，发现空场就通知（或按配置自动下单） |
| 停止 | 中断当前任务 |
| 保存配置到程序 | 把页面上的修改写进 `config.yaml` |

---

## 三、每天自动跑（可选）

如果不想每天手动点「开始抢票」，让系统在 **06:58** 自动启动控制台并开始：

**Windows（任务计划程序）**

- 触发器：每天 06:58
- 操作：启动程序 `C:\path\to\project\.venv\Scripts\python.exe`，参数 `main.py --auto`
- 起始于：`C:\path\to\project`

**macOS（launchd）**：`~/Library/LaunchAgents/com.badminton.booker.plist`

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
    <string>--auto</string>
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

**Linux（cron）**

```bash
58 6 * * * cd /path/to/project && .venv/bin/python main.py --auto
```

`--auto` 的意思是：不开控制台，直接按配置跑一次抢票。

---

## 四、常见问题

| 现象 | 处理 |
| --- | --- |
| 浏览器打不开页面 | 看命令行窗口有没有报错；端口被占用时改 `config.yaml` 里的 `web.port` |
| 提示需要登录 | 程序用持久化 profile，第一次需要手动登录一次（会弹浏览器） |
| 浏览器启动失败 | Chrome 和 chromedriver 版本要一致，把驱动放到 `drivers/` |
| 验证码总是失败 | 改用图鉴：页面「验证码」里选图鉴并填账号 |
| 提交报「存在未支付订单」 | 先去平台取消未支付订单 |
| 没抢到 | 页面日志会显示原因（没读到场次 / 被抢 / 被拒） |

---

## 五、macOS 额外步骤

```bash
brew install --cask google-chrome
brew install --cask chromedriver
xattr -d com.apple.quarantine "$(which chromedriver)"   # 绕过 Gatekeeper
chmod +x start-console.command
```

如果 `ddddocr` 导入报错：`pip install onnxruntime`。

---

## 六、进阶（开发者）

命令行仍然保留，适合排查和自动化：

| 命令 | 作用 |
| --- | --- |
| `python main.py --web` | 启动控制台（等同双击 bat） |
| `python main.py --auto` | 不开控制台，按配置跑一次 |
| `python main.py --check` | 校验配置、打印下一次计划 |
| `python main.py --peek` | 查一次目标日期可订场次（不下单） |
| `python main.py --dry-run` | 演练选场，不提交 |
| `python main.py --poll` | 空场轮询（命令行版） |

配置字段说明见 [`config.example.yaml`](config.example.yaml)。

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
