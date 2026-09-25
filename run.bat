@echo off
rem LLMWiki_BGE-M3 启动入口：RAG 交互式检索（首次运行自动用 raw/ 资料建索引）
rem 用法: run.bat [query.py 的参数，如 --query "问题" --mode dense --k 3]
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "SCRIPT_DIR=%~dp0"

set "PYTHON_EXE=%SCRIPT_DIR%BGE-M3\runtime\env\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo [ERROR] 未找到项目虚拟环境: %PYTHON_EXE% 1>&2
  echo 请先运行 setup.bat 完成安装。 1>&2
  pause
  exit /b 2
)

"%PYTHON_EXE%" "%SCRIPT_DIR%scripts\query.py" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" pause
exit /b %EXITCODE%
