@echo off
rem LLMWiki_BGE-M3 一键安装入口：创建虚拟环境、安装依赖、下载模型
setlocal
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未找到 PowerShell，请使用 Windows 10/11 自带的 PowerShell。 1>&2
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\setup.ps1" %*
exit /b %ERRORLEVEL%
