@echo off
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
py "%~dp0deep_dev_reminder.py"
exit /b %ERRORLEVEL%
