@echo off
set NODE_A_GRPC_HOST=10.8.0.1
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m uvicorn src.server:app --host 0.0.0.0 --port 8000