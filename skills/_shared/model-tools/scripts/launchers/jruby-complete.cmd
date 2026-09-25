@echo off
setlocal
set "RUNTIME_ROOT=%~dp0..\..\.."
set "JAVA=%RUNTIME_ROOT%\tools\temurin-jre\v21.0.11+10\bin\java.exe"
if not exist "%JAVA%" (
  >&2 echo bundled Temurin JRE is missing: %JAVA%
  exit /b 2
)
"%JAVA%" --add-opens java.base/sun.nio.ch=ALL-UNNAMED --add-opens java.base/java.io=ALL-UNNAMED -jar "%~dp0jruby-complete-9.3.8.0.jar" %*
exit /b %ERRORLEVEL%
