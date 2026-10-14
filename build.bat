@echo off
rem ---------------------------------------------------------------------------
rem  Compiles firmware + simulator core into build\fridge_sil.dll and writes the
rem  variable map (build\fridge_sil.dll.symbols.json). Compile step ONLY.
rem  To start the simulator use run.bat; to run the tests use test.bat.
rem
rem  Needs 64-bit MinGW-w64 GCC on PATH (w64devkit or MSYS2 UCRT64).
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where gcc >nul 2>nul
if errorlevel 1 (
    echo [build] gcc not found on PATH.
    echo         Install w64devkit ^(unzip, add its bin folder to PATH^) or MSYS2 UCRT64 GCC.
    exit /b 1
)
for /f "delims=" %%m in ('gcc -dumpmachine') do set GCC_TARGET=%%m
echo %GCC_TARGET% | findstr /b "x86_64" >nul
if errorlevel 1 (
    echo [build] gcc targets %GCC_TARGET%; a 64-bit ^(x86_64^) MinGW-w64 gcc is required.
    exit /b 1
)

call "%~dp0find_python.bat" || exit /b 1

if not exist build mkdir build
echo [build] compiling...
gcc -O0 -g -shared -Wall -Wextra -Isim -Dmain=fw_main ^
    firmware\*.c sim\sim_core.c ^
    -static-libgcc -o build\fridge_sil.dll
if errorlevel 1 (
    echo [build] compile FAILED
    exit /b 1
)

echo [build] extracting variable map...
%PY% host\symbols.py build\fridge_sil.dll
if errorlevel 1 exit /b 1

echo [build] OK: build\fridge_sil.dll
exit /b 0
