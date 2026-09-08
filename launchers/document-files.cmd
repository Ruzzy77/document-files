@echo off
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\python\python.exe" goto packaged
if exist "%ROOT%\.venv\Scripts\python.exe" goto development
echo Document Files runtime missing. Install a platform release. 1>&2
exit /b 127
:packaged
"%ROOT%\python\python.exe" -I "%ROOT%\launchers\run.py" cli %*
exit /b %errorlevel%
:development
"%ROOT%\.venv\Scripts\python.exe" -I "%ROOT%\launchers\run.py" cli %*
exit /b %errorlevel%
