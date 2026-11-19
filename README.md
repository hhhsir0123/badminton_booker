# 羽毛球场地自动预订

每天 07:00 抢两天后的场地。附一个**纯静态**的配置页面：在浏览器里改参数，导出
`config.yaml` 覆盖原文件即可，不需要起任何服务。

> 只用于自己的校园账号，请遵守学校的场馆预约规则。

## 快速开始

```bash
# 1. 安装依赖（Python 3.9+，本机需要 Chrome）
pip install -r requirements.txt

# 2. 准备配置
copy config.example.yaml config.yaml     # Windows
cp config.example.yaml config.yaml       # macOS / Linux
# 也可以直接 python main.py --web，在页面里改完导出

# 3. 打开配置页面
python main.py --web
```

配置页面在 `src/web/index.html`，直接双击也能打开（`file://` 即可，无需服务器）。

## 配置页面

浏览器不能悄悄改磁盘上的文件，所以页面是「载入 → 编辑 → 导出」：

1. 点「载入 config.yaml」，选中项目根目录的配置文件；
2. 改完点「导出 config.yaml」，用下载的文件覆盖原文件；
3. 想先看一眼结果，展开右侧「预览将写入的内容」。

页面上的校验规则和 Python 端一致（时间格式、时段先后、数值范围），
导出前会拦住错误；编辑中的草稿存在 localStorage，刷新不会丢；`Ctrl+S` 直接导出。
页面只渲染常用字段，`email`、`runtime` 等未展示的字段会原样保留。

## 命令行

```bash
python main.py --check    # 校验配置并打印下一次开售时间和候选
python main.py --peek     # 立即查一次目标日期现在还能订什么（不下单）
python main.py --dry-run  # 演练：真的点场格，但不提交订单
python main.py --date 2026-11-21   # 临时指定入场日，不改配置文件
python main.py            # 等到开售，抢一次
python main.py --poll     # 空场轮询
python main.py --web      # 打开配置页面
```

## 每天 7 点自动运行

脚本本身是「跑一次」：启动后等到 07:00 抢一次，抢完退出。让它每天自动跑有两种方式。

**方式一：任务计划程序**（推荐，抢完自动退出，不常驻）

```powershell
$root = "C:\path\to\badminton_booker_github"
$action  = New-ScheduledTaskAction -Execute "python.exe" -Argument "main.py" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 06:58
Register-ScheduledTask -TaskName "羽毛球预约" -Action $action -Trigger $trigger
```

注意：任务要选「只在用户登录时运行」，否则窗口型浏览器不可见；06:58 前电脑不能睡眠。

**方式二：常驻**
先 `python main.py --poll` 或自己写个 while 循环，每天调用一次 `python main.py`。

## 接口抓包结果（2026-11-19 实测）

用你自己的账号实际登录、进入羽毛球馆、点了日期页签，抓到的真实契约：

- 接口前缀是 `/venue-server`：`GET /venue-server/api/reservation/day/info`
- **所有请求都带签名**（`emitAjax`）：

```
sign = MD5(secret + path + 按 key 排序拼接的 key+value + timestamp + " " + secret)
headers: app-key / timestamp / sign / cgAuthorization
```

  实测把抓到的请求用这个公式复算，`sign` 完全一致。
- `day/info` 参数：`venueSiteId=3&searchDate=YYYY-MM-DD&hasReserveInfo=1&nocache=<ms>`，
  返回约 90KB JSON：`spaceTimeInfo`（时段 id）+ `reservationDateSpaceInfo`（每个场格的状态）
  + `token`。状态码：1 可订 / 2 停用 / 3 预约中 / 4 已订 / 33 新预约。
- 提交接口是 `POST /venue-server/api/reservation/order/submit`（JSON body，同样走签名）。
- 验证码是**滑块拼图**（AJ-Captcha）：`/api/captcha/get` 拿图 + token + secretKey，
  `/api/captcha/check` 提交 AES 加密的 `pointJson`；只有当 `isCaptchaCheck=1` 时才会出现。
- 会话在 localStorage 的 `dataSix`（JWT），通过 `cgAuthorization` 头带上。

基于这个结果，`QueryManager.probe()` 已经改成**直接带签名调 day/info**（约 200ms 一次），
不再依赖浏览器里那条发不出去的 fetch。

## 07:00 抢场是怎么做的

时间线（北京时间）：

```
T-120s   启动 Chrome（复用 profile）、恢复登录、进入场馆页、注入页面工具函数
T-30s    用 HTTP Date 头估算本机与服务器的时钟偏差
T-1.5s   开始探测 day/info 接口，一旦放场立刻刷新预约表
T+0s~    读表 → 选场 → 选同伴 → 提交，全部在页面内一次 JS 调用完成
T+4s     仍未提交成功则放弃本轮（不无限重试）
```

相比原来的实现，关键变化：

1. **不在开售瞬间登录**：登录、验证码、进馆全部提前 2 分钟完成，这是最大的一块时间。
2. **不用 WebDriver 轮询页面**：每次 `execute_script` 都是一次往返，原代码在
   「刷新 → 等 1 秒 → 查表 → 点格子 → sleep 2 秒」上要花 6~10 秒。现在等待和点击
   都在页面内完成，Python 只在最后收一次结果。
3. **探测放场而不是死等 07:00:00**：平台放出数据可能晚几百毫秒，死等会漏掉。
4. **保留页面原始时段文本**：`08:00~09:00` 不会因为被归一化成 `08:00-09:00` 后点不到格子。
5. **表头两种布局都支持**：场地为列、场地为行都能识别（旧代码写死为列）。
6. **分页安全**：只在当前页找不到候选时才翻页，会识别禁用的分页箭头，不在热路径上白等超时。
7. **提交一次，结果分四类**（`CREATED / UNAVAILABLE / REJECTED / UNKNOWN`）：
   只有平台明确建单才算成功；结果不明时立刻停手去人工核对，绝不重复下单。
8. **点选验证码**：提交后如果弹出点选验证码，用 ddddocr 识别并点击，然后只观察回执，
   不会重复点提交。

### 还能再快吗

可以，但收益和风险不成比例：

- 直接调用下单接口能再省 300~800ms，但参数、签名、风控都要逆向，平台一改版就全废，
  而且更容易被判定为脚本；
- 多账号并发抢同一时段只是把命中率摊薄，还会明显提高封号风险。

真正决定成败的是「有没有在放场那一秒发出请求」和「登录态是否有效」，这两点已经覆盖。

## 验证码

> 完整踩坑记录与最终方案见 [docs/captcha-solution.md](docs/captcha-solution.md)，图鉴接入见 [docs/ttshitu.md](docs/ttshitu.md)。


如果占场前必须过图形验证码，这一段的**正确率**和**单次耗时**直接决定成败。

**为什么快**

| 做法 | 省下的时间 |
| --- | --- |
| 06:58 进馆后就加载 ddddocr 模型 | 避免 07:00 首次识别卡 1~2 秒 |
| 用页面内 canvas 取图（`toDataURL`） | 截图方式 100~300ms，canvas 约 30~50ms |
| 一次 JS 赋值 + 派发 `input` 事件（Vue 能收到） | 比 `send_keys` 逐字符快 50~100ms |
| 点选用页面内 MouseEvent 点击 | 比 ActionChains 少一次往返 |

**为什么准**

- 同一张图跑 5 种预处理各识别一次，按票数取结果：原图、灰度 2 倍、Otsu 二值化 2 倍、
  反色、中值滤波；
- 结果长度不在 4~6 之间的直接丢弃；
- 大小写不敏感投票，但保留第一次出现的写法（有些平台区分大小写）；
- 不通过就不点提交，直接刷新重试（默认 3 次，每次约 200ms）。

**能不能提前做掉**

进馆后先检查页面上有没有验证码：

- 如果它只在**进入预约页**时出现 → `06:58` 就解决掉，`07:00` 完全不用碰（日志：
  `开售前验证码已通过`）；
- 如果它是**每次提交**才出现 → 日志会写 `开售前页面上没有验证码（大概率是提交时才出现）`，
  那就只能靠预热好的模型 + 投票 + 重试，一次约 250ms。

想看真实数据，跑一次演练，日志里会有：

```
验证码投票: A1B2（3/5 一致）
验证码已通过（第 1 次，识别+填写 210ms）
```

票数经常是 1/5，说明图片质量差或干扰线重，可以：

1. 把 `captcha.max_attempts` 调到 4~5（别超过 5，避免触发风控）；
2. 在 `captcha_images/` 里攒样本（每次识别都会存图），人工标几十张后用 ddddocr
   自带的训练脚本微调，一般能从 80% 提到 95% 以上；
3. 点选类验证码本来就由 `ClickCaptchaHandler`（det + ocr 两个模型）处理，预热时会一起加载。

## 怎么把命中率拉到最高

07:00 是供给固定、需求超额的竞速，**没有脚本能保证 100% 抢到**。能做的只有把每一环的失败率压到接近 0：

| 环节 | 典型失败原因 | 现在的处理 |
| --- | --- | --- |
| 会话 | 07:00 才登录、验证码失败 | 提前 2 分钟登录、复用 Cookie，失败每 15 秒重试到放场前 10 秒 |
| 时钟 | 本机快/慢几百毫秒 | HTTP Date 校准；仍不准就手填 `runtime.clock_offset_ms` |
| 发现放场 | 死等 07:00:00 漏掉 | 探测 `day/info`，间隔 120ms，命中立刻读表 |
| 日期还没上架 | 放场后 UI 才渲染出新页签 | 预热 + 放场后重试到 T+4s |
| 场次被抢 | 比人慢 0.5 秒 | 预热省掉首次渲染；只读当前页；一次 JS 完成选场+提交 |
| 候选不够 | 只配了一个时段 | 多写几条候选；打开 `fallback_any` 保底 |
| 网络抖动 | 单次请求失败 | 窗口内退避重试；任务级异常也会写日志、发飞书 |

配置上最影响成功率的三个开关：

```yaml
booking:
  allow_partial: true    # 2 小时抢不到时 1 小时也接受（强烈建议开）
  fallback_any: true     # 候选都不满足时抢当天最早的一段（保底）
  max_attempts: 3        # 平台有频控，别超过 3
```

**保底策略**：07:00 没抢到不等于没票。每天都有人取消，`python main.py --poll` 每 60 秒扫一遍，
发现空场立刻飞书通知（`is_auto_booking: true` 还能直接下单）。挂一整天，命中率比只抢 7 点高得多。

**开抢前必须确认**：

- 电脑不睡眠、不锁屏（页面节流我们已经关了，但睡眠会停掉一切）；
- 06:58 前关掉 VPN / 代理，校园网直连延迟最低；
- 提前一天跑一次 `python main.py --peek`，确认 profile 里的登录态还能用；
- Windows「自动设置时间」保持开启，必要时点一次「立即同步」。

## 防封号设计

- **单账号、单会话**：同一时间只跑一个任务，避免两个 Chrome 抢同一个账号。
- **持久化 profile**：像正常用户一样带着 Cookie 访问，不是每次全新会话。
- **有界请求**：放场窗口内约 10~20 次轻量请求（间隔 120ms），之后退避到 0.4~1.6 秒；
  轮询模式默认 60 秒一次。和「人类 7 点整狂点刷新」量级相当。
- **不重试风暴**：`max_attempts` 默认 3；结果 `UNKNOWN` 立即停止。
- **有头模式优先**：无头浏览器更容易被识别，默认 `headless: false`。
- **不做验证码绕过**：登录验证码用 ddddocr 正常识别，失败就重试，不接打码平台。

## 飞书通知（可选）

群里加一个自定义机器人，把 Webhook 填进配置，轮询发现空场 / 抢到票都会推一条卡片消息。

1. 飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人，复制 Webhook 地址；
2. 打开配置页（`python main.py --web`），展开「飞书通知」粘贴 Webhook，
   机器人开了「签名校验」再把 secret 填上；
3. 或者直接改 `config.yaml`：

```yaml
feishu:
  webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx"
  secret: ""
  mention_all: false
  notify_availability: true   # 轮询发现空场
  notify_success: true        # 抢到票
  notify_failure: false       # 没抢到也推（每天一条，嫌吵就关）
```

发送失败只写日志、不抛异常，不会拖慢抢场。Webhook 相当于口令，别提交到仓库
（`config.yaml` 已经在 `.gitignore` 里）。

验证：跑一轮 `python main.py --poll`，发现空场时手机上应该立刻收到卡片。

## 配置文件

见 `config.example.yaml`，每个字段都有注释。最常改的几项：

```yaml
booking:
  target_time_in_day: "07:00:00"   # 放场时间
  booking_days_ahead: 2            # 抢后天的场
  advance_time: 120                # 提前 120 秒准备
  preferences:
    - {court: "任意", start: "19:00", end: "21:00"}
runtime:
  release_probe_interval_ms: 120   # 探测间隔，太小容易被频控
  keep_open_after_success: 600     # 抢到后浏览器保留 10 分钟付款
```

## 目录结构

```
src/
├── config.py                  # 默认值 / 校验 / 原子保存
├── core/
│   ├── browser.py             # 持久化 profile、后台不降频
│   ├── login.py               # 会话复用、条件等待登录
│   ├── court_query.py         # 预约表读取（快照 / 翻页 / 放场探测）
│   ├── court_booking.py       # 选场 + 提交 + 回执判定
│   ├── page_js.py             # 注入页面的 JS 工具箱
│   └── booking_result.py      # BookingOutcome
├── pipeline/
│   ├── base_pipeline.py
│   ├── ticket_grabbing_pipeline.py   # 07:00 抢场编排
│   └── periodic_scanning_pipeline.py # 空场轮询
├── utils/
│   ├── feishu_notifier.py           # 飞书群机器人（可选）
│   ├── email_notifier.py
│   └── ocr.py
└── web/
    ├── index.html             # 静态配置页
    ├── app.css
    ├── app.js                 # 表单 / 导入导出 / 草稿
    └── config-io.js           # YAML 解析、生成、校验
```

## 测试

```bash
# Python：时间规划、候选匹配、时段解析、配置校验、飞书签名（56 个用例，不需要浏览器）
python -m unittest discover -s test_files -p "test_*.py"

# 配置页逻辑：YAML 解析/生成往返、默认值与校验、页面元素一致性（18 个用例）
node test_files/test_config_io.mjs
```

## 验证（按顺序，风险从低到高）

### 0. 离线自检：不需要账号

```bash
python -m unittest discover -s test_files -p "test_*.py"   # 43 个用例
node test_files/test_config_io.mjs                          # 16 个用例
python main.py --check
```

`--check` 应打印「下一次开售: …（入场日 …）」和按优先级排列的候选。

### 1. 配置页：不需要账号

打开 `src/web/index.html` → 载入 `config.example.yaml` → 改几个值 → 展开「预览将写入的内容」→ 导出。
把导出的文件和 `config.example.yaml` 对比，除注释外字段应一致；故意把某个候选的结束时间改早，
导出时应该被拦下来。

### 2. 真机只读：会开浏览器，不下单

```bash
python main.py --peek
```

第一次需要登录并识别验证码（识别失败会重试 2 次）。预期日志：
`已进入 羽毛球 场馆页面`、`2026-11-21 共 N 个可订候选`。N 为 0 说明这个日期还没放场或已被抢完，
换一个当前有空的日期继续：

```bash
python main.py --peek --date 2026-11-20
```

### 3. 选场演练：会点格子，绝不提交

```bash
python main.py --dry-run --date 2026-11-20
```

预期日志：`演练：尝试选中 …`、`演练结果: selected`，并且 `logs/dry_run_*.png` 里场格已变成选中态。
如果结果是 `unavailable`，说明选择器和实际页面不一致，把截图发我。

### 4. 真实下单：唯一会产生订单的一步

确认上面都正常后，用你确实想要的时段跑一次（建议先 `max_attempts: 1`）：

```bash
python main.py                    # 等到 07:00 抢
python main.py --date 2026-11-20  # 指定已开售的日期，立即抢
```

成功标志：日志 `预订成功：…`，浏览器保持打开 10 分钟供付款。

### 5. 页面结构自检：平台改版时用

在场馆页面按 F12，Console 里逐条执行：

```js
document.querySelectorAll('.reserveTable .spaceTable table').length   // 应为 1
document.querySelectorAll('.date_box > div').length                   // 日期页签数量
document.querySelector('.reserveBlock')?.className                    // 应含 free / reserved
const r = await fetch('/api/reservation/day/info?searchDate=2026-11-20');
console.log(r.status, (await r.text()).length);                      // 200 表示放场探测可用
```

最后一条如果返回 400/404，探测会自动退化成定时刷新，不影响抢场，只是少一点提前量。

## 常见问题

**页面提示「日期不在当前可预约窗口内」**
目标日期还没放出来。用 `python main.py --check` 确认算出的入场日是否正确。

**找不到场格 / 一直卡在读取预约表**
平台改版会让选择器失效。对照 `src/core/page_js.py` 里的 `.reserveTable`、`.date_box`、
`.reserveBlock` 检查，`logs/` 里也有失败截图。

**Chrome 启动失败，提示 profile 被占用**
上一次的 Chrome 没关干净。关掉所有 Chrome，或删除 `.chrome-profile/SingletonLock`。

**提交成功但没有订单**
`BookingOutcome.UNKNOWN` 时会自动截图到 `logs/unknown_*.png`，请到浏览器里核对订单，
不要重复点提交。
