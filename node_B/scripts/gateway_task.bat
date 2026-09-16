@echo off
cd /d D:\FAST\Semester6\NLP\Project_Laptop\node_B\implementation
D:\FAST\Semester6\NLP\Project_Laptop\node_B\implementation\.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> D:\FAST\Semester6\NLP\Project_Laptop\gateway_out.log 2>&1
