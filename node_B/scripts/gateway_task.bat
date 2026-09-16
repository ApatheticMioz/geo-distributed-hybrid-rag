@echo off
cd /d D:\FAST\semester-project\NLP\project_node_b\node_B\implementation
D:\FAST\semester-project\NLP\project_node_b\node_B\implementation\.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> D:\FAST\semester-project\NLP\project_node_b\gateway_out.log 2>&1
