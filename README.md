# Agent-SIL-PoC
Agent-SIL Proof of Concept: a software-in-the-loop simulator for superloop firmware.

Runs **unmodified** bare-metal superloop firmware (non-RTOS) on the PC, one main-loop iteration
at a time on a virtual clock. While it is paused you can read and write peripheral registers and
any firmware variable, including file-statics. The example target is a fridge controller on an
NXP KE04 (thermostat, 3-minute compressor protection, door alarm).

- Step the firmware loop by loop, or run it for virtual minutes or hours in seconds
- Drive inputs (NTC temperature, ADC channels, door switch) and watch outputs (compressor, light, buzzer)
- Edit firmware RAM mid-run by variable name; names, types and comments come from the debug info
- Script the same thing from Python for automated tests (`host/sil.py`)

## Quick start (Windows)

1. Install **64-bit Python 3.8+** and **64-bit MinGW-w64 GCC** (see [Setup](#setup-on-windows-one-time)).
2. Double-click **`run.bat`**: it builds the simulator and opens the stepping console.
3. Type `help` for the commands, or try the [console example](#console-example).

Run **`test.bat`** for the automated tests.

## Repository layout

```
run.bat             Windows: build if needed + open the stepping console   <- start here
test.bat            Windows: build if needed + run the automated tests
build.bat           Windows: compile only (DLL + variable map)
build.sh            Linux equivalent of build.bat
firmware/main.c     example fridge controller (thermostat + 3-min compressor protection + door alarm)
sim/MKE04Z4.h       SIL replacement for the NXP KE04 device header
sim/sim_core.c      scheduler, virtual clock, peripheral model, host API
sim/sim_port.h      threading layer (native Win32 on Windows, pthreads on Linux)
host/sil.py         Python wrapper (ctypes)
host/symbols.py     reads variable names/addresses/types from the debug info
host/console.py     interactive stepping console
host/test_fridge.py first automated tests
```

## Setup on Windows (one time)

1. **64-bit Python 3.8+** from python.org (tick "Add python.exe to PATH").
2. **64-bit MinGW-w64 GCC.** Easiest: download w64devkit (x64 zip) from its GitHub releases,
   unzip to e.g. `C:\w64devkit`, add `C:\w64devkit\bin` to PATH. MSYS2 UCRT64 gcc also works.
   Check in a new terminal: `gcc --version` and `objdump --version`.
3. Double-click **run.bat** (or run it from a terminal to see errors).

`build.bat` only compiles. `run.bat` compiles when sources changed and then starts the
simulator; `run.bat rebuild` forces a rebuild. MSVC is not supported (the code uses GCC
builtins, and the variable map comes from GCC's DWARF debug info).

Linux: `./build.sh`, then `cd host && python3 console.py` / `python3 test_fridge.py`.

The console starts by listing every command and every firmware variable (type, current
value, and the comment from its declaration in the source). `help` and `vars` show them again.

## Using the console

Time only moves when you `step`, `run` or `tick`. Times accept `500` (ms), `500ms`, `2s`,
`1.5s`, `3m`, `1h`. After every command the console prints the virtual time, loop count, door
state, the output pins and the watched variables.

| Group   | Command | What it does |
|---------|---------|--------------|
| Run     | `step [n]` | run n main-loop iterations (each takes `loopcycles` of virtual time) |
|         | `run <time> [ms/loop]` | run every loop pass for `<time>`; the optional `ms/loop` fast-forwards |
|         | `tick [time]` | advance the virtual clock (SysTick ISR) without running the loop |
|         | `reset` | power-on reset: fresh firmware, time 0, temp 4 C, door closed, default clock |
| Inputs  | `temp <celsius>` | set cabin NTC temperature (drives ADC channel 0) |
|         | `door open\|close` | door switch input (PTB0) |
|         | `adc <ch> <raw>` | set a raw 12-bit ADC input |
| Memory  | `get <var>` | read any firmware global or file-static variable |
|         | `set <var> <value>` | write a firmware variable while the loop is parked |
|         | `vars` | list firmware variables with type, current value and description |
|         | `watch <var> [...]` | choose the variables shown after each command |
|         | `status` | show time, outputs and watched variables |
| Timing  | `clock [MHz]` | show / set the core clock (SysTick period is in core cycles) |
|         | `loopcycles [n]` | show / set core cycles one main-loop pass takes (time per step) |
| Console | `help [command]` | list all commands, or show one |
|         | `quit` | leave the console (also `exit`, Ctrl+Z Enter) |

Variables ending in `_c10` are in tenths of a degree C (`g_setpoint_c10 40` = 4.0 C).

### Console example

```
sil> temp 10                 # cabin NTC -> ADC ch0
sil> step                    # one loop pass (1 ms by default) -> WAIT_PROTECT
sil> run 3m                  # compressor comes on at 3:00
sil> set g_setpoint_c10 120  # edit firmware RAM mid-run
sil> step                    # -> IDLE, compressor off
sil> door open
sil> run 61s                 # buzzer on
sil> get s_door_open_at      # file-static variables work too
sil> loopcycles 4800         # a loop pass takes 4800 cycles = 100 us at 48 MHz
sil> clock 24                # 24 MHz core: same SysTick reload -> 2 ms tick
sil> reset                   # power-on reset: start the simulation over
```

## How it works

1. **Header override.** The real `MKE04Z4.h` casts fixed addresses
   (`#define GPIOA ((GPIO_Type*)0x400FF000u)`). The SIL header keeps the same type and macro
   names but points them at global structs, so firmware compiles unchanged.
2. **Loop boundary = watchdog feed.** The firmware runs on its own thread. `WDOG_Feed()` is
   provided by the simulator instead of the driver; when the firmware calls it, the thread parks
   until the host grants another iteration. Superloops feed the dog once per pass, so no firmware
   edits are needed. (If Beko's code feeds via direct register writes, we need one hook macro.)
3. **Virtual clock.** Time is counted in core clock cycles (`SystemCoreClock`, default 48 MHz).
   `SysTick_Config(ticks)` is provided by the simulator and records the period, so
   `SysTick_Handler()` fires every `ticks` cycles, as on target. Each `step` lets one loop pass
   take `loop_cycles` (default 48000 = 1 ms); SysTicks that fall due in that time fire while
   the loop is parked, then the pass runs. `tick` moves time without running the loop.
   Time only moves when the host calls `step`/`tick`/`run`. Pause = just don't call anything.
4. **Editing state.** Inputs (ADC channels, GPIO `PDIR`) are set through the API. Firmware
   variables, including file-statics, come from the DWARF debug info (`objdump --dwarf=info`,
   done once at build time and cached in `*.symbols.json`): name, address, size and
   signed/unsigned. The load address is found from one exported anchor symbol, so ASLR is fine.
5. **Fresh instance per test.** `Sim()` loads a private, uniquely named copy of the DLL, so every
   test starts from power-on state.
6. **Threads.** On Windows the core uses native critical sections / condition variables, so the
   DLL has no dependency on `libwinpthread-1.dll` and Python loads it without PATH tweaks.

In Python, `run(ms)` steps at the real loop rate until `ms` of virtual time has passed.
`run(ms, ms_per_loop=10)` fast-forwards (SysTick still fires on time, the loop runs every 10 ms).
Use 1 ms resolution near timing edges: in fast-forward the door opening at 180 000 ms was
detected at 180 010 ms.

## Known limits (decisions for the next iteration)

- **Register write side-effects aren't modelled.** A plain struct can't react to writes, so
  `PSOR/PCOR/PTOR`, write-1-to-clear flags and read-to-clear registers don't behave like hardware.
  The ADC works via a small peripheral thread that watches `SC1` while the firmware runs, which is
  fine for one peripheral but racy in general. Options: (a) trap accesses with guard pages and a
  fault handler (Windows VEH), (b) rewrite register accesses to function calls at build time,
  (c) keep a peripheral thread per block. The example firmware uses `PDOR` read-modify-write to
  stay out of this.
- **Interrupts only run between loop passes**, not mid-iteration. Fine for flag-setting ISRs;
  races between ISR and main loop won't reproduce.
- **Blocking waits on time** (`while (g_ms - t < 10);`) deadlock the step, since time only
  advances while parked. The step returns a timeout error after 2 s instead of hanging.
- **Speed:** ~70 µs per loop pass on a 2-core cloud box (thread handoff). Fast-forward covers
  long timers.
- **Variables:** scalars (ints, enums, pointers, floats) can be read/written; arrays and structs
  are listed but not editable yet.
- **Windows status:** build, console and tests run on Windows 11 (w64devkit GCC 16.2, Python 3.15).
- Line-by-line stepping inside an iteration isn't built in; attach gdb to the Python process for
  that, the library is built with `-g -O0`.
