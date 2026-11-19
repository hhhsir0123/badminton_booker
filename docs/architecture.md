# 技术文档：架构、依赖与链路

> 面向维护者。用户视角的安装配置见 [README](../README.md)，验证码细节见
> [captcha-solution.md](captcha-solution.md)。

---

## 一、系统概览

```
目标：每天 07:00 平台放场时，抢到 D+2 的羽毛球场，成功率尽可能高、账号风险尽可能低。
约束：平台是 Vue2 SPA + 自研签名，验证码是 AJ-Captcha 点选文字，有频控和失败锁。
```

设计原则：

1. **读表走 API，点选走 DOM**——读数据用签名请求（快、稳），选场/提交复用页面自己的逻辑（不会漏掉前端校验）；
2. **验证码移出关键路径**——06:5x 预解拿 token，07:00 只做"选场 + 提交"；
3. **不刷接口**——放场前只探测一次基线，放场后按退避读表，验证码只解一次；
4. **每一步都可降级**——预解失败回落现场识别，识别失败通知人工，抢到后浏览器保持打开。

---

## 二、架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        入口层                                        │
│  main.py  ──  --check / --peek / --dry-run / --run / --poll / --web   │
└───────────────┬──────────────────────────────────┬───────────────────┘
                │                                  │
    ┌───────────▼────────────┐        ┌────────────▼─────────────┐
    │ TicketGrabbingPipeline │        │ PeriodicScanningPipeline │
    │ （07:00 抢票）          │        │ （空场轮询）              │
    └───────────┬────────────┘        └────────────┬─────────────┘
                │                                  │
    ┌───────────▼──────────────────────────────────▼─────────────┐
    │                    BasePipeline                             │
    │  配置 / 浏览器生命周期 / 登录 / 场馆导航 / 通知 / 停止信号    │
    └───┬──────────┬───────────┬───────────┬───────────┬──────────┘
        │          │           │           │           │
 ┌──────▼───┐ ┌────▼─────┐ ┌───▼──────┐ ┌──▼───────┐ ┌─▼────────────┐
 │Browser   │ │Login     │ │Query     │ │Booking   │ │SubmitCaptcha │
 │Manager   │ │Manager   │ │Manager   │ │Manager   │ │Flow          │
 │Selenium  │ │登录/会话 │ │读表/翻页 │ │选场/提交 │ │预解/代提交   │
 └──────┬───┘ └────┬─────┘ └───┬──────┘ └──┬───────┘ └─┬────────────┘
        │          │           │           │           │
        │          │      ┌────▼─────┐     │      ┌────▼──────────┐
        │          │      │ApiClient │     │      │ClickWordSolver│
        │          │      │签名/请求 │     │      │本地 OCR 兜底  │
        │          │      └────┬─────┘     │      └────┬──────────┘
        │          │           │           │           │
        │          │           │           │      ┌────▼──────────┐
        │          │           │           │      │TTShituProvider│
        │          │           │           │      │图鉴 typeid=43 │
        │          │           │           │      └───────────────┘
        │          │           │           │
        └──────────┴───────────┴───────────┴──────────► 平台（ggtypt.cumtb.edu.cn）
                                                           │
                                              ┌────────────▼───────────┐
                                              │ 通知层                  │
                                              │ FeishuNotifier / Email  │
                                              └────────────────────────┘
```

**读表**走 `ApiClient`（HTTP + 签名），**选场/提交/验证码**走 `Selenium → 页面 DOM / Vue`。
两条通道各取所长：API 快且不依赖渲染，DOM 能复用前端逻辑（同伴、订单表单）。

---

## 三、模块职责

| 模块 | 职责 |
| --- | --- |
| `src/config.py` | 默认值、深度合并、校验、原子保存 |
| `src/core/browser.py` | Chrome 启动/关闭、profile 持久化、按平台找 chromedriver、反自动化特征 |
| `src/core/login.py` | 登录、会话检测、登录验证码 |
| `src/core/api_client.py` | 请求签名、`GET day/info`、`POST order/submit`、场次解析 |
| `src/core/court_query.py` | 读表、翻页、`probe()`（放场探测） |
| `src/core/court_booking.py` | 选场（DOM）、提交、结果判定、验证码联动 |
| `src/core/submit_captcha.py` | 下单验证码：劫持 `verifySuccess`、预解、代提交、等结果 |
| `src/core/page_js.py` | 注入页面的 JS 助手（快照、选格、提交、等结果） |
| `src/core/booking_result.py` | `BookingOutcome` 枚举（CREATED / UNAVAILABLE / REJECTED / UNKNOWN） |
| `src/pipeline/base_pipeline.py` | 生命周期、登录、导航、通知、停止信号 |
| `src/pipeline/ticket_grabbing_pipeline.py` | 抢票主流程（准备、预解、探测、选场、提交） |
| `src/pipeline/periodic_scanning_pipeline.py` | 轮询主流程（定时扫、判断、自动下单、通知） |
| `src/utils/ocr.py` / `captcha_solver.py` | 登录验证码识别 |
| `src/utils/click_word_solver.py` | 点选验证码本地识别（ddddocr 定位 + 倾斜校正 + RapidOCR） |
| `src/utils/ttshitu_provider.py` | 图鉴打码客户端（typeid=43 闭集匹配） |
| `src/utils/feishu_notifier.py` | 飞书卡片 + HMAC 签名，失败不影响主流程 |
| `src/web/*` | 纯静态配置页（解析/生成 YAML、校验、草稿） |
| `src/web/server.py` | 本地控制台：静态页 + `/api/config`、`/api/start`、`/api/stop`、`/api/status` |

### 控制台（本地服务）

```
浏览器  ──HTTP──▶  127.0.0.1:8765（ThreadingHTTPServer，仅本机）
                    ├─ GET  /                配置页（src/web 静态文件）
                    ├─ GET  /api/config      读出 config.yaml
                    ├─ POST /api/config      校验 + 原子写入
                    ├─ POST /api/start       {"mode":"grab"|"poll"} → 线程里跑 Pipeline
                    ├─ POST /api/stop        调 pipeline.stop()
                    └─ GET  /api/status      状态 + 最近 120 行日志（环形缓冲）
```

- 只用标准库（`http.server`），不引入 Web 框架；
- 启动的流程跑在 daemon 线程里，`Runner.stop()` 调 `pipeline.stop()` 中断；
- 日志通过 `logging.Handler` 收进环形缓冲，页面每 2 秒轮询一次；
- 静态打开（`file://`）时页面自动降级为「只能编辑/导出配置」，不会报错。

---

## 四、系统依赖

### 运行时

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.9+ | 运行环境 |
| Chrome | 任意较新版本 | 浏览器 |
| chromedriver | 与 Chrome 同版本 | Selenium 驱动 |
| selenium | ≥4.15 | 浏览器自动化 |
| PyYAML | ≥6.0.1 | 配置读写 |
| ddddocr | ≥1.4.11 | 验证码检测/识别（带 onnxruntime、opencv、Pillow） |
| rapidocr-onnxruntime | ≥1.4.0 | 中文单字识别（可选，点选兜底用） |

### 外部服务

| 服务 | 必需 | 说明 |
| --- | --- | --- |
| 场馆平台 | ✅ | `https://ggtypt.cumtb.edu.cn` |
| 图鉴打码 | 可选 | 点选验证码，`typeid=43`，约 0.01~0.02 元/次 |
| 飞书机器人 | 可选 | 结果通知 |

### 平台接口约定

```
Base:  https://ggtypt.cumtb.edu.cn/venue-server
签名:  sign = MD5(secret + path + join(sorted(k+v)) + timestamp + " " + secret)
头:    app-key / timestamp / sign / cgAuthorization(JWT, localStorage.dataSix)

GET  /api/reservation/day/info?venueSiteId=&searchDate=YYYY-MM-DD&hasReserveInfo=1
     → ~90KB，含 spaceTimeInfo / reservationDateSpaceInfo / token
POST /api/reservation/order/submit
     → 成功返回 tradeNo
GET  /api/captcha/get?captchaType=clickWord   （点选验证码）
POST /api/captcha/check                        （坐标校验，repCode=0000 通过）
```

---

## 五、抢票链路（TicketGrabbingPipeline）

### 5.1 时序

```
T-advance_time   _prepare()      启动浏览器、恢复/执行登录（重试到 T-10s）
                 _prewarm()      打开目标日期，预热表格/翻页
                 _pre_solve()    劫持 verifySuccess → 打开验证码 → 识别 → 点击 → 存 token
                 _clock_offset() 用服务器 Date 校正本机时钟
T-1.5s           _wait_until()   精度等待到放场前 1.5 秒
T0               _grab()         probe 探测放场 → 读表 → 计算候选
T0+ε             book_court()    选场 → 提交 → 验证码 → 判定结果
```

### 5.2 放场探测

```python
probe = query_manager.probe(play_day)      # GET day/info，约 200ms
# 放场前记录 baseline（返回体大小）
# 放场后返回体从 ~N KB 变成 ~M KB → released(probe, baseline) 为真
```

- 探测间隔 `runtime.release_probe_interval_ms`（默认 200ms）；
- 放场判定：`now >= opening` **或** 返回体大小变化超过基线；
- 窗口 `runtime.release_grace_ms`（默认 4s），期间没读到空场会退避重试（0.4→1.6s）。

### 5.3 选场与提交

```
SELECT_CELLS_JS   在页面里按「场地 + 时段」点中格子（~60ms）
SUBMIT_JS         点提交（~80ms），前端走 formData.isCaptchaCheck → doVerify
验证码            预解 token 可用则直接 submitData；否则现场识别（~0.3s）
结果              BookingOutcome：CREATED / UNAVAILABLE / REJECTED / UNKNOWN
```

成功判定：响应里有 `tradeNo`，或页面出现「提交预约成功」。

### 5.4 预解验证码（关键优化）

```
预解阶段   doVerify(true) → 读图 → 图鉴 typeid=43 → canvasClick → 劫持到 token
放场后     选场 → 点提交（填充 submitForm）→ 读表单 → submitData(token + 表单)
兜底       token 过期/提交失败 → 现场本地 OCR 解一次（不再刷新）
```

token 有效期 180 秒，所以预解可以发生在放场前一两分钟；
实现见 `src/core/submit_captcha.py`，踩坑记录见 [captcha-solution.md](captcha-solution.md)。

---

## 六、轮询链路（PeriodicScanningPipeline）

```
每 polling_interval 秒：
  1. 对 polling_date_offsets 里每一天读表
  2. 检查 polling_time_slots 里的时段是否有空场
  3. 有空场：
       - is_auto_booking = true  → 直接调 BookinManager 下单
       - is_auto_booking = false → 只发通知
  4. 发飞书/邮件，记录日志
  5. 等待下一轮（可被 stop() 中断）
```

与抢票链路的区别：

| | 抢票 | 轮询 |
| --- | --- | --- |
| 触发 | 固定放场时间 | 按间隔循环 |
| 目标 | D+2 的指定候选 | 多个日期的指定时段 |
| 速度要求 | 毫秒级 | 秒级 |
| 验证码 | 预解 + 现场兜底 | 现场识别 |
| 用途 | 抢新放出来的场 | 捡别人取消的空场 |

---

## 七、加速渠道（哪些手段在提速）

| 手段 | 收益 | 代价 / 风险 |
| --- | --- | --- |
| **签名 API 读表** | `day/info` ~200ms，不依赖页面渲染 | 需要维护签名（已实现） |
| **放场探测** | 不用等到 07:00:00 才读表，体量变化即判定 | 200ms 一次探测 ≈ 5 req/s，别调太小 |
| **服务器时钟校正** | 消除本机时钟偏差（秒级） | 需要一次 `Date` 请求 |
| **页面 DOM 选场** | ~60ms/格，复用前端选中逻辑 | 依赖页面结构（已固化在 `page_js.py`） |
| **预解验证码** | 把 0.3~5s 的识别挪出 T0 | 需要劫持 `verifySuccess`（已实现） |
| **组件 `canvasClick`** | 唯一被服务器接受的点击方式 | 坐标要按 `imgWidth/310` 缩放 |
| **可信鼠标事件** | 提交按钮必须用 Selenium 原生点击 | 需先 `scrollIntoView` |
| **图鉴 typeid=43** | 闭集定位，0.2~0.3s，比通用题型准得多 | 1 分钱/次 |
| **本地 OCR 兜底** | 免费、0.3s | 准确率约 50~67%，只做兜底 |

**不建议的加速**（实测无效或有害）：多角度穷举（慢 16 倍只多救 1 张）、模板匹配（字体对不上）、
置信度门控（命中本来就高置信，拦不住错）、验证码刷新重试（触发失败锁）。

---

## 八、成功率保障

```
第 1 层  预解 token（图鉴 43）           —— 最好情况：T0 零验证码耗时
第 2 层  预解失败 → 现场本地 OCR 兜底    —— 多花 0.3s
第 3 层  候选按顺序尝试（max_attempts）  —— 第一候选被抢还有第二候选
第 4 层  fallback_any                    —— 候选都不满足时抢任意空场
第 5 层  飞书通知 + 浏览器保持打开        —— 抢到/没抢到都第一时间知道
```

失败时的行为：

| 情况 | 处理 |
| --- | --- |
| 没读到场次（窗口内） | 标记 missed，发失败通知 |
| 场格已被抢 | 尝试下一个候选 |
| 提交被拒 | 尝试下一个候选 |
| 结果不确定 | 停止重试，提示人工核对（避免重复下单） |

---

## 九、风控与合规

1. **验证码只解一次**：AJ-Captcha 失败会按分钟计数并锁 `get` 接口，所以 `captcha.max_attempts` 默认 1；
2. **探测有下限**：`release_probe_interval_ms` 最小 50ms，默认 200ms；
3. **登录态复用**：持久化 profile，避免反复登录；
4. **只读写自己的数据**：不碰他人订单、不抢已有占用的格子；
5. **结果不确定就停**：宁可人工核对，也不盲目重试。

---

## 十、参数速查

| 参数 | 默认 | 影响 |
| --- | --- | --- |
| `booking.advance_time` | 120s | 提前多久开始准备 |
| `booking.booking_days_ahead` | 2 | 抢 D+N |
| `runtime.release_probe_lead_ms` | 1500ms | 提前多久进入探测等待 |
| `runtime.release_probe_interval_ms` | 200ms | 探测频率 |
| `runtime.release_grace_ms` | 4000ms | 放场后继续尝试的窗口 |
| `runtime.submit_timeout_ms` | 6000ms | 等提交回执 |
| `runtime.keep_open_after_success` | 600s | 抢到后保持浏览器 |
| `captcha.provider` | local | 本地 / 图鉴 |
| `captcha.max_attempts` | 1 | 验证码尝试次数 |
| `polling.polling_interval` | 60s | 轮询间隔 |

---

## 十一、扩展点

| 想改什么 | 改哪里 |
| --- | --- |
| 换场馆/换校区 | `booking.venue_type` + 页面 URL（`base_pipeline` 导航） |
| 加候选策略 | `ticket_grabbing_pipeline.candidates()` |
| 换打码服务 | 实现一个带 `solve(png, words)` 的类，注入 `ClickWordSolver(provider=...)` |
| 加通知渠道 | 参考 `feishu_notifier.py`，在 `base_pipeline._notify()` 挂载 |
| 适配新验证码 | `src/utils/captcha_solver.py`（字符型）或 `click_word_solver.py`（点选） |
