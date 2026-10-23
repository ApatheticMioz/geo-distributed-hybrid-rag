@echo off
rem ============================================================================
rem Launch Node C Gateway (Edge Ingress & Tantivy BM25 Sparse Index)
rem Portable relative paths - no hardcoded absolute drive paths.
rem ============================================================================
setlocal
set SCRIPT_DIR=%~dp0
set NODE_C_DIR=%SCRIPT_DIR%..\..\systems\node_c

cd /d "%NODE_C_DIR%\implementation"
if exist ..\.venv\Scripts\python.exe (
    set PYTHON_BIN=..\.venv\Scripts\python.exe
) else if exist .venv\Scripts\python.exe (
    set PYTHON_BIN=.venv\Scripts\python.exe
) else (
    set PYTHON_BIN=python.exe
)

echo Starting Node C gateway on 0.0.0.0:8000...
"%PYTHON_BIN%" -m uvicorn src.gateway:app --host 0.0.0.0 --port 8000
endlocal
