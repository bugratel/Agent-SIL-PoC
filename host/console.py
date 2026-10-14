"""
Interactive SIL console: step the firmware and edit values while it's paused.

  python console.py
  sil> help
"""
import cmd
import glob
import os
import re
import shlex

from sil import Sim, SimError, PIN_COMP, PIN_LIGHT, PIN_BUZZER, PIN_DOOR, PORT_A

STATES = {0: "IDLE", 1: "WAIT_PROTECT", 2: "COOLING"}
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SOURCE_DIRS = [os.path.join(_ROOT, "firmware"), os.path.join(_ROOT, "sim")]

# commands shown by 'help' / at startup, grouped (descriptions come from the docstrings)
COMMAND_GROUPS = [
    ("Run",      ["step", "run", "tick", "reset"]),
    ("Inputs",   ["temp", "door", "adc"]),
    ("Memory",   ["get", "set", "vars", "watch", "status"]),
    ("Timing",   ["clock", "loopcycles"]),
    ("Console",  ["help", "quit"]),
]

# file-scope declaration with a trailing comment:  int16_t g_x = 40;  /* what it is */
_DECL = re.compile(r"^\s*(?:static\s+|volatile\s+|const\s+)*[A-Za-z_][\w ]*?\b([A-Za-z_]\w*)"
                   r"\s*(?:\[[^\]]*\])?\s*(?:=[^;]*)?;\s*/\*\s*(.*?)\s*\*/")


def var_descriptions(src_dirs=SOURCE_DIRS):
    """name -> comment, taken from the trailing comment on each variable's declaration."""
    out = {}
    for path in (p for d in src_dirs for p in glob.glob(os.path.join(d, "*.c"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _DECL.match(line)
                if m:
                    out.setdefault(m.group(1), m.group(2))
    return out


_UNITS = {"": 1, "ms": 1, "s": 1000, "sec": 1000, "m": 60000, "min": 60000, "h": 3600000}


def parse_time(txt):
    """'500' -> 500 ms, '2s' -> 2000, '1.5s' -> 1500, '3m' / '3min' -> 180000"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*([a-z]*)\s*", txt.lower())
    if not m or m.group(2) not in _UNITS:
        raise ValueError(f"bad time '{txt}' (use e.g. 500, 500ms, 2s, 1.5s, 3m, 1h)")
    return round(float(m.group(1)) * _UNITS[m.group(2)])


class Console(cmd.Cmd):
    prompt = "sil> "

    def __init__(self):
        super().__init__()
        self.descr = var_descriptions()
        self.watch = ["g_cabin_c10", "g_setpoint_c10", "s_state", "s_alarm"]
        self.boot()

    def boot(self):
        """Fresh firmware instance (power-on reset), parked at the start of its main loop."""
        self.sim = Sim()
        self.sim.start()
        self.sim.set_temp(4.0)

    def preloop(self):
        print("Agent-SIL PoC console. Firmware is parked at the start of its main loop;\n"
              "time only moves when you step / run / tick.\n")
        self.print_commands()
        print()
        self.print_vars()
        print("\nTry: temp 10 | step | run 3m | set g_setpoint_c10 60 | door open | run 61s\n"
              "'help' shows the commands again, 'vars' the variables, 'reset' starts over.\n")
        self.status()

    # ---- helpers ---------------------------------------------------------------
    def status(self):
        s = self.sim
        t = s.time_ms
        out = s.gpio_out()
        door = "OPEN" if s.gpio_in() & PIN_DOOR else "closed"
        pins = "  ".join(f"{n}={'ON ' if out & m else 'off'}"
                         for n, m in (("COMP", PIN_COMP), ("LIGHT", PIN_LIGHT), ("BUZZ", PIN_BUZZER)))
        us = s.time_us
        frac = f"{t % 1000:03d}" if us % 1000 == 0 else f"{us % 1000000:06d}"   # us when sub-ms
        print(f"t={t // 60000:02d}:{t // 1000 % 60:02d}.{frac}  loops={s.loops}  door={door}  {pins}")
        print("   " + "  ".join(f"{name}={self.fmt(name)}" for name in self.watch))

    def print_commands(self):
        print("Commands  (times: 500 = 500 ms, 2s, 1.5s, 3m, 1h)")
        for group, names in COMMAND_GROUPS:
            print(f"  {group}")
            for n in names:
                usage, _, what = getattr(self, "do_" + n).__doc__.partition("  ")
                print(f"    {usage:<22}{what.strip()}")

    def fmt(self, name):
        v = self.sim[name]
        return f"{v}({STATES.get(v, '?')})" if name == "s_state" else str(v)

    def print_vars(self):
        names = [n for n in self.sim.variables() if not n.startswith("__")]
        w = max(map(len, names))
        print(f"Firmware variables  ('set <var> <value>' to change, '_c10' = tenths of a degree C)")
        for n in names:
            info = self.sim.symbols[n]
            try:
                val = self.fmt(n)
            except TypeError:
                val = "-"                       # arrays/structs: listed, not readable yet
            descr = self.descr.get(n, "") or ("(array/struct: not readable/editable yet)" if val == "-" else "")
            print(f"  {n:<{w}}  {info['type']:<15} {val:<16} {descr}".rstrip())

    def _do(self, fn):
        fn()
        self.status()

    def onecmd(self, line):
        # a typo must not kill the session (and the firmware state with it)
        try:
            return super().onecmd(line)
        except (SimError, KeyError, TypeError, ValueError, OverflowError) as e:
            print("error:", e)

    def emptyline(self):
        pass                                    # don't repeat the last command on Enter

    # ---- execution -------------------------------------------------------------
    def do_step(self, arg):
        "step [n]          run n main-loop iterations (each takes loopcycles of virtual time)"
        self._do(lambda: self.sim.step(int(arg or 1)))

    def do_tick(self, arg):
        "tick [time]       advance the virtual clock (SysTick ISR) without running the loop"
        self._do(lambda: self.sim.tick(parse_time(arg or "1")))

    def do_run(self, arg):
        "run <time> [ms/loop]  run every loop pass for <time> (optional ms/loop fast-forwards)"
        a = shlex.split(arg)
        if not a:
            return print("usage: run <time> [ms_per_loop]")
        ms = parse_time(a[0])
        mpl = parse_time(a[1]) if len(a) > 1 else None
        self._do(lambda: self.sim.run(ms, ms_per_loop=mpl))

    def do_reset(self, arg):
        "reset             power-on reset: fresh firmware, time 0, temp 4 C, door closed, default clock"
        self.boot()
        print("reset: firmware restarted from power-on")
        self.status()

    def do_clock(self, arg):
        "clock [MHz]       show / set the core clock (SysTick period is in core cycles)"
        if arg.strip():
            self.sim.core_clock = round(float(arg) * 1e6)
        s = self.sim
        tick = s.systick_period
        tick_txt = f"{tick} cycles = {tick * 1e6 / s.core_clock:g} us" if tick else "not configured"
        print(f"core clock {s.core_clock / 1e6:g} MHz, SysTick every {tick_txt}, "
              f"loop pass {s.loop_cycles} cycles = {s.loop_cycles * 1e6 / s.core_clock:g} us")

    def do_loopcycles(self, arg):
        "loopcycles [n]    show / set core cycles one main-loop pass takes (time per step)"
        if arg.strip():
            self.sim.loop_cycles = int(arg, 0)
        self.do_clock("")

    # ---- inputs ----------------------------------------------------------------
    def do_temp(self, arg):
        "temp <celsius>    set cabin NTC temperature (drives ADC channel 0)"    
        self._do(lambda: self.sim.set_temp(float(arg)))

    def do_adc(self, arg):
        "adc <ch> <raw>    set a raw 12-bit ADC input"
        ch, raw = map(int, arg.split())
        self._do(lambda: self.sim.set_adc(ch, raw))

    def do_door(self, arg):
        "door open|close   door switch input (PTB0)"
        self._do(lambda: self.sim.set_door(arg.strip() == "open"))

    # ---- firmware memory ------------------------------------------------------
    def do_get(self, arg):
        "get <var>         read any firmware global or file-static variable"
        try:
            print(f"{arg} = {self.sim[arg.strip()]}")
        except (KeyError, TypeError) as e:
            print("error:", e)

    def do_set(self, arg):
        "set <var> <value>  write a firmware variable while the loop is parked"
        name, val = arg.split()
        self._do(lambda: self.sim.__setitem__(name, int(val, 0)))

    def do_watch(self, arg):
        "watch <var> [...]  choose variables shown after each command"
        if arg.strip():
            self.watch = arg.split()
        self.status()

    def do_vars(self, arg):
        "vars              list firmware variables with type, current value and description"
        self.print_vars()

    def do_status(self, arg):
        "status            show time, outputs and watched variables"
        self.status()

    def do_help(self, arg):
        "help [command]    list all commands, or show one"
        if arg.strip():
            return super().do_help(arg)
        self.print_commands()

    def do_quit(self, arg):
        "quit              leave the console (also: exit, Ctrl+Z Enter)"
        return True

    do_EOF = do_quit
    do_exit = do_quit


if __name__ == "__main__":
    Console().cmdloop()
