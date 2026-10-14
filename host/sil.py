"""
Python side of the SIL: loads the firmware library and drives it.

    sim = Sim()
    sim.start()                 # runs init up to the first loop boundary
    sim.set_temp(10.0)          # NTC input -> ADC channel 0
    sim.run(ms=1000)            # 1 s of virtual time
    sim.step()                  # one superloop iteration (advances loop_cycles of time)
    sim.loop_cycles = 4800      # loop pass takes 4800 cycles = 100 us at 48 MHz
    sim["g_setpoint_c10"] = 60  # edit a firmware variable mid-run
    sim["s_state"]              # read it back (works for file-static vars too)
"""
import ctypes as C
import itertools
import math
import os
import shutil
import struct
import tempfile

import symbols as _symbols

HERE = os.path.dirname(os.path.abspath(__file__))
LIBNAME = "fridge_sil.dll" if os.name == "nt" else "libfridge_sil.so"
DEFAULT_LIB = os.path.normpath(os.path.join(HERE, "..", "build", LIBNAME))

_CTYPES = {
    ("u", 1): C.c_uint8, ("u", 2): C.c_uint16, ("u", 4): C.c_uint32, ("u", 8): C.c_uint64,
    ("i", 1): C.c_int8,  ("i", 2): C.c_int16,  ("i", 4): C.c_int32,  ("i", 8): C.c_int64,
    ("f", 4): C.c_float, ("f", 8): C.c_double,
}
_instance = itertools.count()

# port index / pin masks of the example firmware
PORT_A = 0
PIN_COMP, PIN_LIGHT, PIN_BUZZER, PIN_DOOR = 1 << 0, 1 << 1, 1 << 2, 1 << 8
NTC_CH = 0

_ERRORS = {
    -1: "firmware did not reach the next loop boundary (blocking wait / infinite loop?)",
    -2: "firmware main() returned",
    -3: "firmware is running; call needs it parked",
    -4: "already started",
    -5: "call start() first",
}


class SimError(RuntimeError):
    pass


def _u32(x, minimum=0):
    """Validate a value for a uint32 C argument (ctypes would silently wrap it)."""
    x = int(x)
    if not minimum <= x <= 0xFFFFFFFF:
        raise ValueError(f"{x} out of range ({minimum}..{0xFFFFFFFF})")
    return x


def ntc_raw(temp_c, r25=10e3, beta=3950.0, r_pull=10e3, full_scale=4095):
    """Plant side: cabin temperature -> 12-bit ADC reading for the NTC divider."""
    r = r25 * math.exp(beta * (1 / (temp_c + 273.15) - 1 / 298.15))
    return round(full_scale * r / (r + r_pull))


class Sim:
    def __init__(self, lib_path=DEFAULT_LIB):
        if not os.path.exists(lib_path):
            raise FileNotFoundError(f"{lib_path} not found - run build.bat (Windows) or build.sh first")
        if os.name == "nt" and struct.calcsize("P") != 8:
            raise RuntimeError("64-bit Python is required (the DLL is built 64-bit)")

        self.symbols = _symbols.load(lib_path)      # from DWARF, cached as .symbols.json

        # Load a private, uniquely named copy so every Sim gets fresh firmware globals
        # (= a power-on reset). Unique names stop Windows reusing an already-loaded DLL.
        self._tmp = tempfile.mkdtemp(prefix="sil_")
        stem, ext = os.path.splitext(os.path.basename(lib_path))
        path = os.path.join(self._tmp, f"{stem}_{os.getpid()}_{next(_instance)}{ext}")
        shutil.copy2(lib_path, path)
        self.lib = L = C.CDLL(path)

        for fn, args, res in [
            ("sim_start", [], C.c_int),
            ("sim_step", [C.c_uint32], C.c_int),
            ("sim_tick", [C.c_uint32], C.c_int),
            ("sim_run", [C.c_uint32], C.c_int),
            ("sim_run_coarse", [C.c_uint32, C.c_uint32], C.c_int),
            ("sim_time_ms", [], C.c_uint64),
            ("sim_time_us", [], C.c_uint64),
            ("sim_cycles_now", [], C.c_uint64),
            ("sim_get_core_clock", [], C.c_uint32),
            ("sim_set_core_clock", [C.c_uint32], None),
            ("sim_get_loop_cycles", [], C.c_uint32),
            ("sim_set_loop_cycles", [C.c_uint32], None),
            ("sim_get_systick_period", [], C.c_uint32),
            ("sim_loop_count", [], C.c_uint64),
            ("sim_set_timeout_ms", [C.c_uint32], None),
            ("sim_set_adc", [C.c_uint32, C.c_uint32], None),
            ("sim_set_gpio_in", [C.c_uint32, C.c_uint32, C.c_uint32], None),
            ("sim_get_gpio_out", [C.c_uint32], C.c_uint32),
            ("sim_get_gpio_in", [C.c_uint32], C.c_uint32),
        ]:
            f = getattr(L, fn)
            f.argtypes, f.restype = args, res

        # debug-info addresses are link-time; the DLL/.so is relocated when loaded
        anchor = C.addressof(C.c_uint32.in_dll(L, "sim_anchor"))
        self._base = anchor - self.symbols["sim_anchor"]["addr"]

    # ---- execution control --------------------------------------------------
    def _chk(self, rc):
        if rc:
            raise SimError(_ERRORS.get(rc, f"error {rc}"))

    def start(self):            self._chk(self.lib.sim_start())
    def step(self, n=1):
        """n main-loop passes; each advances the clock by loop_cycles core cycles."""
        self._chk(self.lib.sim_step(_u32(n)))
    def tick(self, ms=1):
        """Advance the clock (SysTick fires on schedule) without running the loop."""
        self._chk(self.lib.sim_tick(_u32(ms)))
    def run(self, ms, ms_per_loop=None):
        """Advance virtual time by ms. Default: one loop pass per loop_cycles, as on target.
        ms_per_loop fast-forwards (one pass per that many ms, SysTick still fires on time)."""
        if ms_per_loop is None:
            self._chk(self.lib.sim_run(_u32(ms)))
        else:
            self._chk(self.lib.sim_run_coarse(_u32(ms), _u32(ms_per_loop)))

    @property
    def time_ms(self):          return self.lib.sim_time_ms()
    @property
    def time_us(self):          return self.lib.sim_time_us()
    @property
    def cycles(self):           return self.lib.sim_cycles_now()
    @property
    def loops(self):            return self.lib.sim_loop_count()
    @property
    def systick_period(self):   return self.lib.sim_get_systick_period()

    @property
    def core_clock(self):
        """Core clock in Hz (SystemCoreClock). Default 48 MHz."""
        return self.lib.sim_get_core_clock()
    @core_clock.setter
    def core_clock(self, hz):   self.lib.sim_set_core_clock(_u32(hz, minimum=1))

    @property
    def loop_cycles(self):
        """Core cycles one main-loop pass takes (= time per step). Default 48000 = 1 ms."""
        return self.lib.sim_get_loop_cycles()
    @loop_cycles.setter
    def loop_cycles(self, n):   self.lib.sim_set_loop_cycles(_u32(n, minimum=1))

    # ---- I/O -----------------------------------------------------------------
    def set_adc(self, ch, raw):           self.lib.sim_set_adc(ch, raw)
    def set_temp(self, temp_c):           self.set_adc(NTC_CH, ntc_raw(temp_c))
    def set_door(self, is_open):
        self.lib.sim_set_gpio_in(PORT_A, PIN_DOOR, PIN_DOOR if is_open else 0)
    def gpio_out(self, port=PORT_A):      return self.lib.sim_get_gpio_out(port)
    def gpio_in(self, port=PORT_A):       return self.lib.sim_get_gpio_in(port)
    def pin(self, mask, port=PORT_A):     return bool(self.gpio_out(port) & mask)

    # ---- firmware variables (any global or file-static symbol) -------------
    def var(self, name):
        info = self.symbols.get(name)
        if info is None:
            raise KeyError(f"no variable '{name}' in firmware")
        ctype = _CTYPES.get((info["kind"], info["size"]))
        if ctype is None:
            raise TypeError(f"'{name}' is {info['type']}, not a scalar; "
                            "only scalar variables can be read/written for now")
        return ctype.from_address(self._base + info["addr"])

    def __getitem__(self, name):          return self.var(name).value
    def __setitem__(self, name, value):   self.var(name).value = value

    def variables(self):
        """Firmware variables (everything except the simulator's own)."""
        return sorted(n for n in self.symbols if not n.startswith(("sim_", "port_")))
