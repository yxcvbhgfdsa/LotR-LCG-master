@echo off
chcp 65001 >nul
title 魔戒：卡牌版
echo ========================================
echo        魔戒：卡牌版 启动脚本
echo ========================================
echo.

REM 设置 Qt 平台插件路径
set QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins
echo 已设置 Qt 平台插件路径:
echo %QT_QPA_PLATFORM_PLUGIN_PATH%
echo.

REM 检查 Python 环境
if exist "%~dp0.venv\Scripts\python.exe" (
    echo 使用虚拟环境 Python
    echo.
    echo 正在启动游戏...
    echo.
    "%~dp0.venv\Scripts\python.exe" "%~dp0主脚本.py"
) else (
    echo 警告: 未找到虚拟环境，使用系统 Python
    echo.
    echo 正在启动游戏...
    echo.
    python "%~dp0主脚本.py"
)

if errorlevel 1 (
    echo.
    echo ========================================
    echo        启动失败！
    echo ========================================
    pause
)
