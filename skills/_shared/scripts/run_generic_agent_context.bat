@echo off
setlocal
if "%KB_ROOT%"=="" set "KB_ROOT=%~dp0..\..\.."
for %%I in ("%KB_ROOT%") do set "KB_ROOT=%%~fI"
cd /d "%KB_ROOT%"
if "%~1"=="" (
  echo Usage: run_generic_agent_context.bat "task description"
  echo.
  echo This prints the registry route and reminds any external agent to read AGENTS.md.
  exit /b 1
)
python "%KB_ROOT%\skills\_shared\scripts\agent_skill_router.py" %*
echo.
echo Give the target agent this instruction:
echo   Work in %KB_ROOT%. Read AGENTS.md, then use the primary skill above. Do not load all skills.
endlocal
