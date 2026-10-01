@echo off
cd /d D:\geo-distributed-hybrid-rag\node_b\node_B\implementation
D:\geo-distributed-hybrid-rag\node_b\node_B\implementation\.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> D:\geo-distributed-hybrid-rag\node_b\gateway_out.log 2>&1
