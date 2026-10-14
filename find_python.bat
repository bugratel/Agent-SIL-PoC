@echo off
rem Sets PY to a 64-bit Python 3 command (py launcher preferred, then python).
set PY=
py -3 -c "import struct,sys; sys.exit(struct.calcsize('P')!=8)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import struct,sys; sys.exit(sys.version_info<(3,8) or struct.calcsize('P')!=8)" >nul 2>nul && set "PY=python"
if not defined PY (
    echo [python] 64-bit Python 3.8+ not found. Install from python.org ^(tick "Add to PATH"^).
    exit /b 1
)
exit /b 0
