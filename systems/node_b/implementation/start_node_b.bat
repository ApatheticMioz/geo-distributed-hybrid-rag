@echo off
set NODE_A_GRPC_HOST=10.8.0.1
set NODE_A_GRPC_PORT=50052
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    set PYTHON_BIN=.venv\Scripts\python.exe
) else (
    set PYTHON_BIN=python.exe
)
"%PYTHON_BIN%" -m uvicorn src.server:app --host 0.0.0.0 --port 8000
