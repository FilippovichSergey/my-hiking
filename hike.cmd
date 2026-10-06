@echo off
rem Usage: hike komoot / youtube / new / build / serve
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\hike.py" %*
