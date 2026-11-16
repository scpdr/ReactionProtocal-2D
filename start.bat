@echo off
rem Reaction Protocol - Windows 启动脚本
rem 双击本文件即可开服；参数会原样传给 server.py（例如：start.bat --no-browser）
chcp 65001 >nul
cd /d "%~dp0"

if not exist server.py (
  echo 找不到 server.py：请确认 start.bat 与 server.py 在同一个文件夹里。
  pause
  exit /b 1
)

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo 未找到 Python 3。请先安装 Python 3 并勾选 "Add Python to PATH"。
  echo 下载：https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

echo 使用 %PY% 启动 Reaction Protocol ...
echo （关闭本窗口即停止开服）
%PY% server.py %*
pause
