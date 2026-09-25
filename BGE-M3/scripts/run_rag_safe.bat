@echo off
setlocal
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
set "BGE_ROOT=%SCRIPT_DIR%.."
set "PYTHON_EXE=%BGE_ROOT%\runtime\env\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo BGE-M3 venv python not found: %PYTHON_EXE% 1>&2
  exit /b 2
)

"%PYTHON_EXE%" "%SCRIPT_DIR%rag_pipeline.py" %*
exit /b %ERRORLEVEL%
