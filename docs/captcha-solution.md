# 抢票验证码：踩坑记录与最终方案

> 时间：2026-11-19 ｜ 场景：中国矿业大学（北京）智慧体育场馆预约平台
> 目标：07:00 放场时抢到两天后的场地，验证码环节要**又快又准**
> 结论：**06:59 预解验证码 → 07:00 用 token 直接提交**，验证码完全不占 T0 时间

---

## 一、最终方案（一张图）

```
06:59:00  打开预约页（已在 06:58 登录并停在目标场馆）
          ├─ 劫持 verifySuccess（只存 token，不提交）
          ├─ doVerify(true) 打开验证码
          ├─ 图鉴 typeid=43 识别坐标（0.2~0.3s）→ canvasClick 点击
          ├─ 通过 → 拿到 captchaVerification + captchaToken + 真实表单
          └─ 失败 → 换 1~2 次图（认不出不计失败次数；点错才计）

07:00:00  探测放场 → 选场 → 选同伴
          ├─ 点提交（唤起验证码，同时 submitForm 被填充）
          └─ 立刻用预解 token 调 submitData → 「提交预约成功」

07:00:0x  兜底：预解 token 失效/提交失败 → 现场本地 OCR 解验证码（0.3s）
```

关键点：**验证码只解一次，且不在 07:00 解**。

---

## 二、目标验证码是什么

平台用的是开源项目 [AJ-Captcha](https://github.com/anji-plus/captcha) 的「点选文字」：

| 属性 | 值 | 来源 |
| --- | --- | --- |
| 验证码类型 | clickWord（点选文字） | `/api/captcha/get?captchaType=clickWord` |
| 字库 | 内置约 500 个常用汉字（`HAN_ZI`） | `ClickWordCaptchaServiceImpl.HAN_ZI` |
| 每张图字数 | 4 个，其中 **1 个是干扰字**（不在词表） | 源码 `wordCount` 逻辑 |
| 字体 | `WenQuanZhengHei.ttf`（可配置） | `AbstractCaptchaService.clickWordFontStr` |
| 字号 | `HAN_ZI_SIZE = 25` | 同上 |
| 旋转 | 每个字随机 ±45° | 源码 `AffineTransform.rotate` |
| 校验容差 | x/y 各 ±25px | 源码 `verification` |
| 一次校验 token 有效期 | **120 秒**（`EXPIRESIN_SECONDS`） | 同上 |
| 二次校验 token 有效期 | **180 秒**（`EXPIRESIN_THREE`） | 同上 |
| 内置限流 | 按分钟限 get/check/verify，**失败会锁 get 接口**（`REQ_GET_LOCK_SECONDS`） | `Const.java` |

> 这两条是方案成立的基础：token 有效期 180 秒，且与"哪一天、哪个场次"无关，只证明"这个人通过了验证码"。

---

## 三、踩过的坑（按严重程度排序）

### 1. 用合成 MouseEvent 点击，服务器永远判错

**症状**：坐标算对了、`checkPosArr` 也记录了正确坐标，但服务器一直返回 `6111 验证失败`。

**原因**：前端读的是 `event.offsetX / offsetY`，不是 `clientX/clientY`：

```js
getMousePos = (e, t) => ({ x: t.offsetX, y: t.offsetY })
```

合成 `MouseEvent` 的 `offsetX` 不可靠。

**修复**：走组件自己的方法

```js
vm.canvasClick({ offsetX: x * imgWidth / 310, offsetY: y * imgHeight / 155 })
```

`pointTransfrom()` 会把画布坐标换算回 **310×155** 的服务器坐标系。

---

### 2. 画布是 330×155，图是 310×155

**症状**：按 310 算比例，点击位置整体偏右约 6%，全部失败。

**原因**：`setSize.imgWidth` 是 `330px`（容器宽度），而验证码图片是 310×155。

**修复**：比例要用 `imgWidth / 310`（不是固定 310），见上面代码。

---

### 3. `verifySuccess` 劫持得太晚，token 拿不到

**症状**：预解流程走完，`window.__bbVerify` 一直是 `null`。

**原因**：验证码组件是**懒创建**的。第一次 `doVerify` 时才 mount，`success` prop 在 mount 时绑定。
如果先点提交（创建组件）再劫持，绑定的还是原函数。

**修复**：**必须在组件创建前劫持**。

```python
flow.patch()      # 先劫持
button.click()    # 再点提交（创建组件）
```

---

### 4. 页面上有多个验证码组件实例

**症状**：读到的词表是「落/论/新」，点完却提示「请依次点击【爬,使,究】」。

**原因**：DOM 里有多个 VerifyPoints 实例（隐藏的旧实例 + 可见的新实例）。遍历顺序不保证拿到可见的那个。

**修复**：优先选 `canvas.getClientRects().length > 0` 的实例。

---

### 5. 验证码通过后弹窗关闭，`refresh()` 救不回来

**症状**：预解成功后想换下一张，调用 `refresh()` 却读不到图。

**原因**：成功回调里父组件会把弹窗关掉（`showVerify = false`）。

**修复**：每张之间**重新点提交**（重新 `doVerify`），不要依赖 `refresh()`。

---

### 6. 点错一次，验证码会自己刷新

**症状**：点错后 `state.text` 变成新的三个字，误以为是"读到了旧实例"。

**原因**：AJ-Captcha 校验失败后会 `refresh()` 换一张图。

**修复**：判断"是否通过"要看 `verifySuccess` 是否被调用（`window.__bbVerify`），不要看 `state.text`。
另外，**认不出就别点**（点错会计入失败次数，可能触发 get 接口锁定）。

---

### 7. 图鉴 `typeid=20` 会挑中干扰字

**症状**：平台返回 3 个坐标，第 2 个落在干扰字上，实测 8 张里有 6 张和本地 OCR 不一致（差 84~264px）。

**原因**：`typeid=20` 是"通用点选 3 坐标"——**平台不知道你要找哪三个字**，只能自己猜。

**修复**：改用 **`typeid=43`**（传标题文本的通用点选），把目标词一起发过去：

```json
{
  "typeid": "43",
  "image": "<base64>",
  "content": "\\u5199\\u9ed1\\u679c"
}
```

- `content` 是目标词按顺序拼接，**中文必须转成 `\uXXXX`**；
- 平台只负责**定位**，不负责**识别**，任务难度完全不同。

**效果**：与本地 OCR 一致性从 2/8 提升到 **8/8**，偏差从 84~264px 降到 **2~7px**，耗时从 2.5~5.8s 降到 **0.19~0.30s**。

---

### 8. Chrome 自动升级，chromedriver 版本不匹配

**症状**：`SessionNotCreatedException: This version of ChromeDriver only supports Chrome version 152 / Current browser version is 158`。

**原因**：Chrome 在后台自动更新了。

**修复**：

1. 下载匹配版本（storage.googleapis.com 可直连，大文件会截断，用 `curl -C -` 续传）；
2. 放到 `drivers/chromedriver-win64/chromedriver.exe`；
3. `BrowserManager._service()` 优先用本地驱动，避免 Selenium Manager 联网。

```powershell
https://storage.googleapis.com/chrome-for-testing-public/<version>/win64/chromedriver-win64.zip
```

---

### 9. 缺少 `--no-sandbox`，Chrome 起来就崩

**症状**：`Chrome failed to start: crashed (DevToolsActivePort file doesn't exist)`。

**修复**：`BrowserManager._options()` 增加 `--no-sandbox` 和 `--disable-gpu`。

---

### 10. Selenium `execute_script` 不写 `return` 就返回 None

**症状**：选场、读词表全部返回 `None`，但浏览器里手动执行却正常。

**原因**：Selenium 把脚本包成函数，只有显式 `return` 才有返回值（CDP 的 `Runtime.evaluate` 才会返回最后一个表达式的值）。

**修复**：所有注入脚本写成 `return (function(){ ... })();`。

---

### 11. 提交按钮在视口外，点不到

**症状**：点击提交没反应，验证码不弹。

**原因**：按钮 `getBoundingClientRect().y = 1854`，而窗口只有 1000 高。

**修复**：先 `scrollIntoView({block:'center'})`，再用 Selenium 原生 `element.click()`（可信事件）。

---

### 12. 不选同伴，提交按钮是"哑"的

页面底部有「同伴 最少1人最多5人」，不选同伴时点提交没有任何反应，也不报错。

**修复**：选场后先点 `.companion_box .buddy-row`，手机号是预填的，不用管。

---

### 13. 未支付订单会挡住新订单

**症状**：提交返回 `预约失败，您存在未支付的订单！`

**原因**：平台限制同一用户同时只能有一个未支付订单。

**修复**：测试前先取消历史未支付订单（页面 `/venue/orders` → 取消）。

---

### 14. 本地 OCR 到不了 95%，模板匹配也不行

实测数据（真实样本）：

| 方案 | 单字 | 整张（3 字全中） | 耗时 |
| --- | --- | --- | --- |
| ddddocr det + ocr | 0% | 0% | — |
| RapidOCR 整图 | — | 33% | 1.3s |
| ddddocr 定位 + 倾斜校正 + RapidOCR 投票 | 87% | 50~67% | 0.3s |
| 加模板兜底 | 覆盖率 53%→73% | 但真机触发时**2/2 点错** | +50ms |
| 纯字体模板匹配（WenQuanZhengHei） | — | 66% | — |
| 倒角距离形状匹配 | — | 33% | — |

**结论**：现成 OCR + 通用字体模板都到不了 95%，**识别必须是闭集任务**（告诉它找哪几个字），这就是 `typeid=43` 的价值。

---

### 15. 评测口径踩过的坑（重要）

- 一开始把"平台返回了 3 个坐标"当成"识别成功" → 得出 89% 的假指标，实际坐标经常是错的；
- 用 OCR 高置信结果当标签去评测模板匹配 → 标签本身有错，评测失真；
- 小样本（15 张）的 95% 置信区间约 ±13%，67% 和 53% 不矛盾。

**正确口径**：以服务器 `repCode == "0000"`（或 `verifySuccess` 被调用）为准。

---

## 四、代码地图

| 文件 | 作用 |
| --- | --- |
| `src/utils/ttshitu_provider.py` | 图鉴客户端：`typeid=43` + `content`（unicode）、坐标解析、错误码映射 |
| `src/utils/click_word_solver.py` | 本地兜底识别：ddddocr 定位 + 倾斜校正 + RapidOCR + 按词表匹配 |
| `src/core/submit_captcha.py` | **核心**：`patch()` 劫持、`pre_solve()` 预解、`submit_selected()` 代提交、`wait_success()` |
| `src/pipeline/base_pipeline.py` | `_build_click_word_solver()`：按 `captcha.provider` 选本地/平台并预热 |
| `src/pipeline/ticket_grabbing_pipeline.py` | `_pre_solve_captcha()`（T-60s 预解）、`_submit_presolved()`（T0 代提交）、失败回落 `book_court` |
| `test_files/bench_captcha_compare.py` | 本地 vs 平台对比（逐张结果 + JSON） |
| `test_files/bench_presolve_live.py` | 真机预解通过率统计（不下单） |
| `test_files/pp_pipeline_live.py` | 乒乓球真机全链路（会下单） |
| `test_files/diag_captcha_live.py` | 诊断：平台 vs 本地坐标、点击结果 |

---

## 五、配置

```yaml
captcha:
  provider: ttshitu        # local = 只用本地 OCR；ttshitu = 图鉴
  fallback_to_local: true
  max_attempts: 1          # AJ-Captcha 失败会锁 get 接口，别重试
  pre_solve: true
  ttshitu:
    username: "你的用户名"
    password: "你的登录密码"
    type_id: 43            # 关键：43 才传词表
    timeout_seconds: 20    # 走代理时 8s 不够
```

---

## 六、真机验证结果（2026-11-19）

### 全链路（乒乓球，真实下单）

```
15:11:29  选 11/21 08:00 气膜馆1号 + 同伴 → 点提交
15:11:30  验证码：儿 / 嘴 / 阵
15:11:30  图鉴 typeid=43 → (x,y)×3，265ms → 点击 → 通过
15:11:31  劫持 verifySuccess → token=338dd598...
15:11:31  代提交 → 「提交预约成功」
15:11:32  订单 D261119000244（40 元，未支付）
```

### token 可复用性（核心实验）

```
T      解出验证码，捕获 token（不提交）
T+10s  用同一 token + 真实表单调 submitData
结果   {"code":200,"tradeNo":"D261119000236",...}  → 成功
```

**结论：验证码可以提前解，token 留到后面用**（有效期 180 秒，实测 10 秒后可用）。

### 预解通过率（typeid=43）

```
#1 通过 1735ms  words=['装','生','放']
#3 通过 1172ms  words=['倒','可','加']
```

样本量还小（2/2），需要继续攒。

---

## 七、运维手册

### 首次部署

1. 注册图鉴 <https://ttshitu.com/>，充值（点选约 0.01~0.02 元/次）；
2. 下载与 Chrome 版本匹配的 chromedriver 到 `drivers/chromedriver-win64/`；
3. 填 `config.yaml` 的 `captcha.ttshitu`；
4. 跑 `python test_files/demo_ttshitu.py --image <样本> --dry-run` 验证参数；
5. 跑 `python test_files/bench_presolve_live.py` 验证真机预解。

### 每日 07:00

- 06:58 启动任务（登录 + 停在目标场馆）；
- 06:59 自动预解（最多 3 次换图）；
- 07:00 放场探测 → 选场 → 代提交；
- 预解失败 → 现场本地 OCR 兜底；
- 抢到后浏览器保持打开（`runtime.keep_open_after_success`），及时付款。

### 排障

| 现象 | 检查 |
| --- | --- |
| 验证码不弹 | 是否选了同伴？提交按钮是否在视口外？ |
| 点击一直 6111 | 是否用了 `canvasClick` + `imgWidth/310` 缩放？ |
| 拿不到 token | `verifySuccess` 是否在组件创建前劫持？ |
| 平台报"缺少必要参数:content" | `type_id` 是否为 43 且传了 `words`？ |
| 平台报"用户名或密码错误" | 账号是否填的是登录密码？ |
| 提交报"存在未支付订单" | 先取消历史未支付订单 |
| 浏览器启动崩溃 | chromedriver 版本？`--no-sandbox`？profile 是否被占用？ |

---

## 八、还没做完的事

1. **预解通过率样本量不足**：目前 2/2，需要 30~50 张统计真实数字；
2. **羽毛球链路未跑通真实 07:00**：代码已接通，但要在真实放场时间验证一次；
3. **本地 OCR 可以再优化**：目前 50~67%，作为兜底够用，但不是主力；
4. **订单清理**：测试产生的未支付订单需要取消，否则会挡住下一次。
