@echo off
cd /d D:\dd-keyword-tool
set PYTHONIOENCODING=utf-8
set HEADLESS=1
D:\python\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 >> D:\dd-keyword-tool\server.log 2>&1
