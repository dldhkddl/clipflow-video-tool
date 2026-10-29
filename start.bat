@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install -e ".[test]"
python -m clipflow.app
