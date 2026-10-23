@echo off
rem ============================================================================
rem Launch Node B Gateway (FastAPI BGE-M3 + Qdrant + RRF)
rem Portable relative paths - no hardcoded absolute drive paths.
rem ============================================================================
setlocal
set SCRIPT_DIR=%~dp0
set NODE_B_DIR=%SCRIPT_DIR%..\..\systems\node_b\implementation

set QDRANT_HOST=127.0.0.1
set NODE_A_GRPC_HOST=10.8.0.1
set NODE_A_GRPC_PORT=50052

cd /d "%NODE_B_DIR%"
if exist .venv\Scripts\python.exe (
    set PYTHON_BIN=.venv\Scripts\python.exe
) else (
    set PYTHON_BIN=python.exe
)

echo Starting Node B gateway on 0.0.0.0:8000...
"%PYTHON_BIN%" -m uvicorn src.server:app --host 0.0.0.0 --port 8000
endlocal
