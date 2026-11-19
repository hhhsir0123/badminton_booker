#!/bin/bash
# macOS / Linux：双击运行，启动本地控制台并自动打开浏览器
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

echo "正在启动控制台，浏览器会自动打开 http://127.0.0.1:8765/"
echo "关闭这个终端窗口即可退出程序。"
exec "$PY" main.py --web
