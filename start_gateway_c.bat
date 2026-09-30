@echo off
rem ============================================================================
rem start_gateway_c.bat — Node C (sparse_user_facing) gateway launcher.
rem
rem Invoked by the Windows scheduled task `pdc_gateway_c` on Node C
rem (WireGuard 10.8.0.3). Mirrors the Node B `start_gateway.bat` pattern:
rem cd to the node implementation dir, then start uvicorn with the node's
rem own venv python.
rem
rem   * module : src.gateway:app  (Node C FastAPI gateway)
rem   * host   : 0.0.0.0          (from systems/node_c/config.yaml -> gateway.host)
rem   * port   : 8000             (from systems/node_c/config.yaml -> gateway.port)
rem
rem The venv lives at systems/node_c/.venv (one level ABOVE implementation/).
rem ============================================================================
cd /d "C:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\systems\node_c\implementation"
"C:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\systems\node_c\.venv\Scripts\python.exe" -m uvicorn src.gateway:app --host 0.0.0.0 --port 8000
