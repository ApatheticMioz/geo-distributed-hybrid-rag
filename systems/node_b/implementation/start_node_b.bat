@echo off
set NODE_A_GRPC_HOST=10.8.0.1
set NODE_A_GRPC_PORT=50052
cd /d "D:\Work\Semester6\NLP\Project_Laptop\systems\node_b\implementation"
.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000
