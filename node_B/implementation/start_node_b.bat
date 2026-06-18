@echo off
set NODE_A_GRPC_HOST=10.8.0.1
cd /d "d:\FAST\Semester6\NLP\Project_Laptop\node_B\implementation"
.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000