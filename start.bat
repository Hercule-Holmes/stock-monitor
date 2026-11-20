@echo off
chcp 65001 >nul
title 股票筛选系统
cd /d %~dp0
echo 正在启动股票筛选系统 (手动启动, 非开机自启)...
echo 启动后浏览器访问: http://127.0.0.1:8600
echo 关闭本窗口即可停止服务。
echo.
set PYTHONIOENCODING=utf-8
python -m app.main
pause
