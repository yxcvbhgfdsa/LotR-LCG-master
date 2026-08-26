@echo off
cd /d "%~dp0"
set QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins
".venv\Scripts\python.exe" "主脚本.py" 