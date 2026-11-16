#!/usr/bin/env bash
# Reaction Protocol · 启动脚本（Linux / 银河麒麟 / deepin / Ubuntu 等）
#
# 最稳的用法（不受"双击打开的是编辑器"影响）：
#     bash start.sh
# 或先加执行权限再双击：
#     chmod +x start.sh
#
# 可选参数（会原样传给 server.py）：
#     bash start.sh --no-browser     只开服，不自动开浏览器
#     bash start.sh 8770             换个端口（默认 8765）
#     bash start.sh --bots 4         房间里放 4 个 AI
#     bash start.sh --tick 30        低配机器用 30Hz（省 CPU）
set -u
cd "$(dirname "$0")" || exit 1

if [ ! -f server.py ]; then
  echo "找不到 server.py：请确认 start.sh 与 server.py 在同一个文件夹里。"
  read -r -p "按回车退出..." _ 2>/dev/null
  exit 1
fi

# 自动挑一个可用的 Python 3（麒麟自带 python3；没有就提示装）
PY=""
for c in python3 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
      PY="$c"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "未找到 Python 3。"
  echo "银河麒麟 / Ubuntu 系可执行：  sudo apt install -y python3"
  read -r -p "按回车退出..." _ 2>/dev/null
  exit 1
fi

echo "使用 $PY 启动 Reaction Protocol ..."
echo "（关闭本终端窗口即停止开服）"
exec "$PY" server.py "$@"
