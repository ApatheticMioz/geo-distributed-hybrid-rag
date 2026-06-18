@echo off
set NODE_A_GRPC_HOST=10.8.0.1
cd /d "d:\FAST\semester-project\NLP\project_node_b\node_B\implementation"
.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000