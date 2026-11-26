# 纯接口下单 PoC 报告

**目标**：不点页面、不整页刷新，放场后直接 `POST /api/reservation/order/submit` 拿订单。

**结论**：4 个关键环节已确认 3 个，`orderPin` 的算法已完全复现；
只剩「验证码 token 能否直接通过接口拿到」需要一次真机验证。

---

## 一、已经确认的环节

### 1. 下单请求的 payload（浏览器抓包）

```json
{
  "buddyIds": "998138",
  "buddyUids": "",
  "orderPin": "2cb1e7…54de",
  "orderPrice": 60,
  "phone": "173…630",
  "reservationDate": "2026-11-24",
  "reservationOrderJson": "[{\"spaceId\":\"23\",\"timeId\":\"15994\"}]",
  "reservationType": -1,
  "totalAmt": null,
  "venueSiteId": 3,
  "weekStartDate": "2026-11-24",
  "captchaVerification": "…"
}
```

### 2. `spaceId` / `timeId` 的来源（接口可得）

- `spaceId`：`day/info` 的 `reservationDateSpaceInfo[日期]` 行键
- `timeId`：`day/info` 的 `spaceTimeInfo[].id`（按 beginTime/endTime 匹配）
- `buddyIds`：`day/info` 的 `orderParamView.buddyList[].id`
- `venueSiteId`：羽毛球 = 3
- `weekStartDate`：等于 `reservationDate`

### 3. `orderPin` 不是服务端 token，而是「点击坐标的 AES 密文」★

前端代码（`chunk-c3869bc6.js`）：

```js
submitData: function (e) {
  var s = this.commonMethods.keySetPassword(e.clientX + "," + e.clientY);
  ...
  this.$refs.reservationForm.beforeSubmit({ ..., orderPin: s })
}
```

`keySetPassword`（`chingo.js` 的 `ee` 函数）：

```js
function ee(e) {
  var n = CryptoJS.enc.Utf8.parse(Y);   // key
  var t = CryptoJS.enc.Utf8.parse(H);   // iv
  var c = CryptoJS.enc.Utf8.parse(e.toString());
  return CryptoJS.AES.encrypt(c, n, {iv: t, mode: CBC, padding: Pkcs7}).ciphertext.toString();
}
```

key/iv 来自学校配置 `src/config/school-config/cumt.js`（打在 `h["a"]` 里）：

```js
k: ["c1h","2i","5n6g","2o","2k","4a7"],   // key = k.join("")
v: ["C2","H3","I4N","5G2O","3K","1E4"],   // iv  = v.join("")
```

于是：

```
key = "c1h2i5n6g2o2k4a7"
iv  = "C2H3I4N5G2O3K1E4"
orderPin = AES-128-CBC-PKCS7(key, iv, "clientX,clientY").hex()
```

已实现：`src/core/order_pin.py`，并与 openssl 交叉验证：

```
$ printf '512,384' | openssl enc -aes-128-cbc -K <key> -iv <iv> | xxd -p
20acc57d323c0fcdfea5ee4c40a2d1e6
$ build_order_pin(512, 384)
20acc57d323c0fcdfea5ee4c40a2d1e6
```

### 4. 接口签名（已有实现）

`ApiClient` 已实现 `app-key / timestamp / sign / cgAuthorization`，与页面请求一致，
所以 `day/info`、`order/submit` 都可以直接调用。

---

## 二、还没确认的环节

### 验证码（唯一门槛）

页面的提交流程：

```
点击提交 → 弹出 AJ-Captcha（clickWord）→ 用户点 3 个坐标
        → 前端调 /api/captcha/check 拿到 captchaVerification
        → 带着 captchaVerification 调 /api/reservation/order/submit
```

未知点：

1. 直接 `POST /api/reservation/order/submit`（不带 captchaVerification）是否会被拒；
2. `/api/captcha/get` + `/api/captcha/check` 能否用 ApiClient 直接调（是否需要额外签名/会话绑定）；
3. captchaVerification 的有效期与是否一次一用。

---

## 三、PoC 完成度

| 环节 | 状态 |
| --- | --- |
| `spaceId` / `timeId` 映射 | ✅ 确认（day/info） |
| `orderPin` 算法 | ✅ 完全复现（AES，openssl 交叉验证） |
| 接口签名 | ✅ 已有 ApiClient |
| payload 组装 | ✅ `build_submit_payload()` + 单测 |
| 验证码 token | ❓ 待真机验证 |

---

## 四、下一步实验（需要 1~2 次真机，会占用一次下单机会）

1. **混合模式验证**：页面解一次验证码拿到 `captchaVerification`，然后用纯 Python
   （`ApiClient` + `build_submit_payload`）提交，看服务端是否接受 `orderPin`；
   - 成功 → 说明 orderPin/签名都没问题，只剩验证码
   - 失败 → 看返回 code/message，判断是 captcha 还是 orderPin 被拒
2. **验证码接口化**：直接调 `/api/captcha/get` 拿图，本地识别（ttshitu/本地模型）
   后调 `/api/captcha/check`，看能否拿到 `captchaVerification`
3. 全部通过后：抢票流程变成「探测 → 拼 payload → 提交」，
   理论 T0 后 **300ms 内**拿到订单，不再依赖页面刷新

## 五、风险

- 服务端可能校验点击轨迹/设备指纹（`orderPin` 只是坐标密文，未必足够）
- AJ-Captcha 的 `check` 可能绑定客户端指纹（clientUid）
- 平台改版会导致常量/接口失效（本次 key/iv 来自前端 bundle，属于硬编码）
- 纯接口下单绕过了页面的二次确认逻辑，若平台判为异常可能封号，需谨慎小量测试
