@echo off
setlocal
set "RUNTIME_ROOT=%~dp0..\..\.."
set "JAVA=%RUNTIME_ROOT%\tools\temurin-jre\v21.0.11+10\bin\java.exe"
set "BATIK_ALL=%~dp0lib\batik-all-1.19.jar"
set "BRIDGE=%~dp0bridge"
if not exist "%JAVA%" (
  >&2 echo bundled Temurin JRE is missing: %JAVA%
  exit /b 2
)
if not exist "%BATIK_ALL%" (
  >&2 echo Apache Batik runtime is missing: %BATIK_ALL%
  exit /b 2
)
if /I "%~1"=="--java-argfile" (
  if "%~2"=="" (
    >&2 echo --java-argfile requires a UTF-8 Java argument file
    exit /b 2
  )
  if not exist "%~2" (
    >&2 echo Java argument file is missing: %~2
    exit /b 2
  )
  "%JAVA%" -cp "%~dp0lib\*;%BRIDGE%" "@%~2"
  exit /b %ERRORLEVEL%
)
"%JAVA%" -cp "%~dp0lib\*;%BRIDGE%" org.llmwiki.bemarkdown.BatikBridge %*
exit /b %ERRORLEVEL%
