@echo off
rem Builds if needed, then runs the automated tests.
setlocal
cd /d "%~dp0"
if not exist build\fridge_sil.dll (call build.bat || goto :fail)
call find_python.bat || goto :fail
cd host
%PY% test_fridge.py
set RC=%ERRORLEVEL%
pause
exit /b %RC%

:fail
pause
exit /b 1
