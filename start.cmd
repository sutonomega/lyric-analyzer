@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failure
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failure
.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
if errorlevel 1 goto failure
.venv\Scripts\python.exe -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
if errorlevel 1 goto failure
exit /b 0
:failure
echo Startup failed. Check Python 3.12 and the messages above.
pause
exit /b 1
