@echo off
setlocal
set "ALPHA_HOME=C:\Users\diane\alpha_code"
set "ALPHA_VENV=%ALPHA_HOME%\.venv"
set "PATH=%ALPHA_VENV%\Scripts;%PATH%"
"%ALPHA_VENV%\Scripts\alpha.exe" %*