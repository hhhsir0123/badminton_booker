# 羽毛球场地自动预订系统

## 功能特性

- ✅ 自动登录（支持OCR验证码识别）
- ✅ 场地查询
- ✅ 自动预订
- ✅ 邮件通知
- ✅ 配置化管理

## 项目结构

```
badminton_booker/
├── src/
│   ├── __init__.py
│   ├── config.py              # 配置管理
│   ├── core/
│   │   ├── browser.py         # 浏览器管理
│   │   ├── login.py           # 登录模块
│   │   ├── query.py           # 查询模块
│   │   └── booking.py         # 预订模块
│   └── utils/
│       ├── logger.py          # 日志工具
│       ├── ocr.py             # OCR识别
│       └── email_notifier.py  # 邮件通知
├── config.yaml                # 配置文件
├── main.py                    # 主程序入口
├── requirements.txt           # 依赖列表
└── README.md                  # 说明文档
```

## 安装

```bash
# 安装依赖
pip install -r requirements.txt
```

## 配置

编辑 `config.yaml` 文件，填写你的配置信息：

```yaml
user:
  username: "你的用户名"
  password: "你的密码"
  email: "接收通知的邮箱"

# 其他配置...
```

## 使用

### 正常模式

```bash
python main.py
```

### 测试模式

```bash
python main.py --test
```

## 注意事项

1. 确保已安装 Chrome 浏览器
2. 首次运行会自动下载 ChromeDriver
3. 邮件通知需要配置SMTP服务器信息
4. 建议使用QQ邮箱等支持SMTP的邮箱

## 许可证

MIT License