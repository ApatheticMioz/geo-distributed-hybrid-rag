@echo off
cd /d D:\FAST\Semester6\NLP\Project_Laptop\systems\node_b\implementation
D:\FAST\Semester6\NLP\Project_Laptop\systems\node_b\implementation\.venv\Scripts\python.exe -m uvicorn src.server:app --host 0.0.0.0 --port 8000
