#!/bin/sh
# Linux/macOS equivalent of build.bat (compile + variable map). Run: cd host && python3 console.py
set -e
cd "$(dirname "$0")"
mkdir -p build
gcc -O0 -g -fPIC -shared -pthread -Wall -Wextra -Isim -Dmain=fw_main \
    firmware/*.c sim/sim_core.c -o build/libfridge_sil.so
python3 host/symbols.py build/libfridge_sil.so
echo "built build/libfridge_sil.so"
