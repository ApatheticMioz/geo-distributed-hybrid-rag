@echo off
cd /d D:\geo-distributed-hybrid-rag\node_b\systems\node_b\implementation
D:\geo-distributed-hybrid-rag\node_b\systems\node_b\implementation\.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> D:\geo-distributed-hybrid-rag\node_b\gateway_out.log 2>&1
