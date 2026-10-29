@echo off
setlocal
set CLIPFLOW_OFFLINE=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install -e ".[test]"
start "" "http://127.0.0.1:8765"
python -m clipflow.app

