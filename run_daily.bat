@echo off
setlocal
cd /d "%~dp0"
if not exist "data" mkdir "data"
".venv\Scripts\python.exe" -m clipflow.daily >> "data\daily.log" 2>&1
exit /b %errorlevel%
