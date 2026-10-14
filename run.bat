@echo off
rem One-click: builds if sources changed (or no DLL yet), then opens the stepping console.
setlocal
cd /d "%~dp0"
set NEED_BUILD=
if not exist build\fridge_sil.dll set NEED_BUILD=1
if "%1"=="rebuild" set NEED_BUILD=1
if not defined NEED_BUILD (
    %SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command ^
      "$d=(Get-Item 'build\fridge_sil.dll').LastWriteTime; if (Get-ChildItem firmware,sim -Recurse -Include *.c,*.h | Where-Object {$_.LastWriteTime -gt $d}) {exit 1}" ^
      || set NEED_BUILD=1
)
if defined NEED_BUILD (
    call build.bat || (pause & exit /b 1)
)
call find_python.bat || (pause & exit /b 1)
cd host
%PY% console.py
