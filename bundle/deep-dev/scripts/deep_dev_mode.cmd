@echo off
setlocal
"%LocalAppData%\Programs\Python\Python313\python.exe" "%~dp0deep_dev_mode.py" %*
exit /b %ERRORLEVEL%
