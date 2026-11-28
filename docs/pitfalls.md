# 踩坑记录（持续更新）

按“现象 → 根因 → 处理”记录。每条都是真机踩过的，不是推测。

---

## 一、环境与依赖

### 1. Python 3.13 装不上 rapidocr-onnxruntime
- **现象**：`pip install -r requirements.txt` 在 py3.13 下报 `rapidocr-onnxruntime>=1.4.0` 找不到
- **根因**：该包没有 3.13 的 wheel
- **处理**：venv 改用 Python 3.11（旧 venv 保留为 `.venv.bak-313`）

### 2. PyPI 下载只有 ~17KB/s
- **处理**：`-i https://mirrors.aliyun.com/pypi/simple/`

### 3. rapidocr 1.2.3 的 `use_det` 参数被忽略
- **根因**：API 在 1.4.x 才稳定
- **处理**：锁 `rapidocr-onnxruntime>=1.4.11`（且 Python ≤3.12）

---

## 二、登录

### 4. 登录页有两个 tab：统一身份认证 / 校外用户
- **现象**：打开登录页没有任何输入框
- **根因**：默认停在「统一身份认证」SSO，手机号账号在「校外用户」tab
- **处理**：先点「校外用户」，再找 `input[placeholder='手机号']`

### 5. 验证码图片改成 `blob:` 后登录必失败
- **现象**：`验证码输入框或图片找不到`，连续两次后登录失败
- **根因**：平台把验证码图从 `data:image` 换成了 `blob:`，旧选择器（`img[src*='captcha']`、`img[src^='data:image']`）全匹配不到；且图片不在 `ivu-form-item` 里
- **处理**：
  - `IMAGE_SELECTORS` 加 `img[src^='blob:']`
  - 兜底：按尺寸筛「像验证码」的图，挑离输入框最近的
  - 找不到时最多等 1.5s（切 tab 后图片是异步渲染的）
  - 截图前等 `naturalWidth > 0`
  - OCR 空结果时 250ms 后重取一次再决定刷新

### 6. 登录后「未找到场馆入口: 羽毛球」
- **现象**：登录成功，但进入场馆失败
- **根因**：首页场馆列表异步渲染，第一次找不到
- **处理**：最多重试 3 次（每次等 6s，失败刷新 `/venue/home` 再找）

### 7. 两个进程共用一个 Chrome profile → 互相挤掉
- **现象**：轮询报 `invalid session id: session deleted as the browser has closed the connection`
- **根因**：Chrome 同一个 `--user-data-dir` 只能有一个实例；第二个进程启动会把第一个的浏览器顶掉
- **处理**：第二个轮询用独立 profile（`scripts/poll_sunday.py` → `.chrome-profile-sunday`）；诊断脚本不要在主进程运行时用同一个 profile

---

## 三、选场

### 8. 提交按钮/须知「未就绪」，订单永远不提交
- **现象**：`提交按钮/预约须知未就绪（terms-unchecked）`，重试 3 次后放弃
- **根因（真因）**：抢票流程调 `_book_candidate()` 时**漏传 `confirm_terms=True`**，一路传到 `bb.submit(timeout, false)`，`prepareSubmit(false)` 看到须知没勾就直接返回，一次都没点
- **误导点**：诊断日志里 `termsDisabled=False`，说明不是页面禁用，而是“没人去勾”
- **处理**：`_book_candidate(..., confirm_terms=True)`；并保留 `[指标] 提交面板={...}` 日志，方便下次一眼看出

### 9. 勾选了但按钮仍 disabled（iview Checkbox）
- **现象**：`termsChecked: false`，点 `input[type=checkbox]` 无效
- **根因**：iview 3 的 Checkbox 是隐藏 input + 外层 `<label>`，直接点 input 不触发 Vue 的 change
- **处理**：点 `input.closest('label')`（或 `.ivu-checkbox`）；实测点完 `checked:true, btn` 从 `btn disab` 变 `btn`

### 10. 乒乓球提交没反应（nextStep 门闩）
- **现象**：点提交无验证码、无请求，6~8 秒超时
- **根因**：`submitData(e)` 第一行是 `if (this.nextStep && this.selected.length)`；
  `nextStep` 就是「已阅读并同意」勾选框的 v-model，普通用户初值 false
- **处理**：同 #9（点 label 让 nextStep 变 true）；诊断时读活跃组件的 `nextStep/selected/$refs.reservationForm`

### 11. 乒乓球必须选同伴
- **现象**：提示「同伴不能小于1人」，提交不往下走
- **根因**：乒乓球馆 `buddyNumMin=1`（羽毛球也是 1，但抢票流程默认带了同伴）
- **处理**：下单前 `click_partner([名字])`；接口 payload 里 `buddyIds` 就是同伴 id

### 12. 预约须知弹层挡住提交按钮
- **现象**：Selenium 报 `element click intercepted`，被 `<span style="font-family:宋体">` 挡住
- **根因**：`.proContent` 的须知正文覆盖了按钮坐标
- **处理**：用 JS 派发 `mousedown/mouseup/click`（`bb.submit` 就是这么做的），或先点「关闭」

### 13. `bb` 不是全局变量
- **现象**：`typeof bb === 'undefined'`，脚本报错
- **根因**：`page_js.py` 的注入脚本是 IIFE，只暴露 `window.__bb`
- **处理**：外部脚本统一用 `window.__bb.xxx`

---

## 四、验证码

### 14. 先 patch `verifySuccess` 再手动 `submitData` → 250 验证码非法校验
- **根因**：平台自己的提交流程还会做一次 token 绑定校验，绕过去就被判非法
- **处理**：不要手动提交，点提交让平台自己走；我们只负责解验证码

### 15. 验证码解得太快 → 验证码非法校验
- **根因**：点选验证码的图片面板有入场动画，坐标还没稳定就点
- **处理**：拿到图后停 1.2s 再点

### 16. 现场解时图片还没加载就截屏 → OCR 空
- **现象**：`第 1 次识别失败`，重试几次才成
- **处理**：`solve_now` 先等图片出现（≤5s）+ `naturalWidth>0`；OCR 空时 250ms 后重取

### 17. 解完只看页面提示 → 判不出成功/失败
- **处理**：优先读平台下单接口回执（`window.__bb.orderResponses`），`code=200 且 data 非空` 才算建单

---

## 五、抢票时序

### 18. 放场瞬间页面还没就绪，读表读到空
- **现象**：07:00 刷新后读表为空/没选到，报“场格被抢走”
- **根因**：日期栏刷新后表格还在渲染
- **处理**：刷新后轮询等目标页签出现（≤5s）；点页签后等数据响应；不要用固定 sleep

### 19. 预解 token 被整页刷新作废
- **现象**：`预解未完成：…页面已刷新`
- **根因**：D+2 放场时目标日期还不在日期栏，必须整页 reload，reload 会销毁验证码组件
- **处理**：T-60s 先看日期栏，目标日期不在就直接跳过预解、改为提交时现场解（已验证 ~0.3s）

### 20. DOM 读表用了 2.9 秒
- **现象**：`放场→候选就绪=2944ms`，热门时段早被抢光
- **处理**：放场探测那次 `day/info` 顺手解析场格，直接出候选（`候选来源=探测响应`），2.9s → 0.5s

### 21. 选场失败要等 750ms/格
- **现象**：3 个候选失败花了 4.5 秒
- **处理**：`selectCell` 先看 class，不是 `free` 立刻返回 `not-free`；抢票 `cell_wait_ms=200`，失败从 1.5s → ~8ms

### 22. 只试了 3 个候选
- **现象**：14 个候选只点了 3 个就结束
- **根因**：`booking.max_attempts` 默认 3
- **处理**：`runtime.grab_max_attempts` 默认 10

### 23. 并行刷新要等探测完成才刷新
- **处理**：T0+30ms 后台线程先 reload，和探测并行，探测确认后 join（省 ~250ms）

---

## 六、配置

### 24. 页面保存配置会丢掉 `runtime.*` 实验开关
- **现象**：`runtime.patch_date_bar` / `overlap_release_reload` 保存后消失
- **根因**：`config-io.js` 不会 emit 这些键，保存时整个 `runtime` 被覆盖
- **处理**：目前只能直接改 `config.yaml`；若要长期用，需要把开关加进前端表单

### 25. 固定日期写死导致抢错天
- **现象**：配置里 `booking_date=2026-11-26`，程序去抢“明天”而不是 D+2
- **处理**：抢 D+2 时把「指定日期」留空

### 26. 轮询每轮都整页刷新
- **现象**：`点击日期页签没有触发数据请求（no-response），改用整页刷新`
- **说明**：页面已经停在目标日期时点页签不会触发请求，属预期；轮询场景可接受

---

## 七、纯接口下单（PoC）

### 27. `orderPin` 不是服务端 token
- **根因**：前端 `keySetPassword(e.clientX + "," + e.clientY)`，即**点击坐标的 AES-CBC 密文**
- **key/iv**：来自 `school-config/cumt.js` 的 k/v 数组（`c1h2i5n6g2o2k4a7` / `C2H3I4N5G2O3K1E4`）
- **验证**：Python 计算结果与 openssl 一致

### 28. 用 JSON 提交下单接口 → 401 签名未通过
- **根因**：`/order/submit` 要 **form-urlencoded**；签名是对**表单字段**算的
- **验证**：用抓包参数重算 sign，与真实 sign 完全一致（`64867737...`）
- **处理**：POST 用 `urlencode`，签名用同一份字段

### 29. 验证码接口形状
- `GET /api/captcha/get?captchaType=clickWord&clientUid=point-<uuid>&ts=..&nocache=..`
  → `data.repData.{secretKey, originalImageBase64, token}`
- `POST /api/captcha/check`（form）：`captchaType=clickWord&pointJson=<b64>&token=<token>`
  - `pointJson = base64(AES-ECB-PKCS7(secretKey, JSON([{x,y},...])))`（已用抓包解密验证）
- `captchaVerification = AES-ECB(secretKey, token + "---" + pointJson)`（前缀已验证一致，后段需用 check 响应的 pointJson）

---

## 八、运维

### 30. 诊断脚本不要在主程序跑的时候用同一个 profile
- 今天两次 `invalid session id` 都是这么来的，直接把用户正在跑的轮询搞死
- **规矩**：先 `pgrep -f src.web.server` / `pgrep -f chrome-profile`，确认没在跑，或用独立 profile

### 31. 定时任务会在启动时重启控制台
- **坑**：抢票进程正在等待放场时，定时任务到点重启控制台 → 把等待中的进程杀掉
- **处理**：手动启动后暂停定时任务；或让定时任务只做“检查+启动”不做“重启”
