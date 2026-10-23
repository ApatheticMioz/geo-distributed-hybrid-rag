@echo off
set SCRIPT_DIR=%~dp0
set NODE_B_DIR=%SCRIPT_DIR%..\implementation
set LOG_FILE=%SCRIPT_DIR%..\..\gateway_out.log

cd /d "%NODE_B_DIR%"
if exist .venv\Scripts\python.exe (
    set PYTHON_BIN=.venv\Scripts\python.exe
) else (
    set PYTHON_BIN=python.exe
)

"%PYTHON_BIN%" -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> "%LOG_FILE%" 2>&1
