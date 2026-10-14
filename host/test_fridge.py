"""
First digitised tests: each test boots a fresh firmware instance (power-on reset),
drives inputs, advances virtual time and checks outputs / internal state.

  python test_fridge.py          (or: pytest test_fridge.py)
"""
import time

from sil import Sim, PIN_COMP, PIN_LIGHT, PIN_BUZZER

IDLE, WAIT_PROTECT, COOLING = 0, 1, 2


def boot(temp_c=4.0):
    sim = Sim()
    sim.start()
    sim.set_temp(temp_c)
    sim.step()
    return sim


def test_compressor_restart_protection():
    sim = boot(10.0)                                   # warm cabin at power-up
    assert sim["s_state"] == WAIT_PROTECT
    sim.run(179_000, ms_per_loop=10)
    assert not sim.pin(PIN_COMP), "compressor started before 3 min protection"
    sim.run(1_000)                                     # 1 ms resolution around the edge
    assert sim.pin(PIN_COMP) and sim["s_state"] == COOLING


def test_hysteresis():
    sim = boot(10.0)
    sim.run(180_000, ms_per_loop=1)
    assert sim.pin(PIN_COMP)
    sim.set_temp(3.5); sim.run(100)                    # inside band: keep cooling
    assert sim.pin(PIN_COMP)
    sim.set_temp(2.9); sim.run(100)                    # below 4.0 - 1.0: stop
    assert not sim.pin(PIN_COMP) and sim["s_state"] == IDLE
    sim.set_temp(4.9); sim.run(100)                    # inside band: stay off
    assert not sim.pin(PIN_COMP)


def test_setpoint_edit_midrun():
    sim = boot(10.0)
    sim.run(180_000, ms_per_loop=10)
    assert sim["s_state"] == COOLING
    sim["g_setpoint_c10"] = 120                        # 12.0 C, written while paused
    sim.step()
    assert sim["s_state"] == IDLE and not sim.pin(PIN_COMP)


def test_door_alarm_after_60s():
    sim = boot()
    sim.set_door(True); sim.run(10)
    assert sim.pin(PIN_LIGHT) and not sim.pin(PIN_BUZZER)
    sim.run(59_980, ms_per_loop=10)
    assert not sim.pin(PIN_BUZZER), "alarm too early"
    sim.run(20)
    assert sim.pin(PIN_BUZZER) and sim["s_alarm"] == 1
    sim.set_door(False); sim.step()
    assert not sim.pin(PIN_LIGHT) and not sim.pin(PIN_BUZZER)


def test_step_advances_clock():
    sim = Sim()
    sim.start()
    assert sim.systick_period == 48_000                # firmware: SysTick_Config(48000u)
    sim.step(5)                                        # default pass = 48000 cycles = 1 ms
    assert sim.time_ms == 5 and sim["g_ms"] == 5
    sim.loop_cycles = 4_800                            # 100 us per pass at 48 MHz
    sim.step(9)
    assert sim.time_us == 5_900 and sim["g_ms"] == 5   # next SysTick not due yet
    sim.step()
    assert sim.time_us == 6_000 and sim["g_ms"] == 6
    sim.run(10)
    assert sim.time_ms == 16 and sim["g_ms"] == 16 and sim.loops == 5 + 10 + 100


def test_systick_follows_core_clock():
    sim = Sim()
    sim.start()
    sim.core_clock = 24_000_000                        # same reload, half the clock
    sim.run(100)
    assert sim.time_ms == 100 and sim["g_ms"] == 50    # SysTick now every 2 ms


if __name__ == "__main__":
    failed = 0
    for name, fn in [(n, f) for n, f in globals().items() if n.startswith("test_")]:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    raise SystemExit(failed)
