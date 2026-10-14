/*
 * SIL core: runs the firmware's main() on its own thread and lets the host
 * advance it one superloop iteration at a time on a virtual clock.
 *
 *   host thread                     firmware thread
 *   -----------                     ---------------
 *   sim_start()  ───────────────▶   fw_main(): init ... WDOG_Feed()  [parks]
 *   sim_step(1)  clock += one pass (SysTicks due fire while parked)
 *                ───────────────▶   one loop pass ... WDOG_Feed()    [parks]
 *   (edit registers / variables freely while parked)
 *
 * A small peripheral thread models hardware that reacts to register writes
 * while the firmware is running (here: ADC conversion complete).
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include "sim_port.h"      /* Win32 threads on Windows, pthreads elsewhere */
#include "MKE04Z4.h"

/* ---- Peripheral register blocks ----------------------------------------- */
GPIO_Type sim_GPIOA, sim_GPIOB, sim_GPIOC;
ADC_Type  sim_ADC = { .SC1 = 0x1F };             /* ADCH = 0x1F: module off */

/* Known exported symbol; the host uses it to locate every other symbol
 * (including file-static firmware variables) from the library's symbol table. */
API volatile uint32_t sim_anchor = 0x51A51A51u;

/* ---- Firmware entry points (renamed with -Dmain=fw_main) ---------------- */
extern int  fw_main(void);
extern void SysTick_Handler(void);

/* ---- Scheduler state ----------------------------------------------------- */
static port_thread_t sim_fw_th, sim_per_th;
static port_mutex_t  sim_mtx;
static port_cond_t   sim_cv_fw;     /* wakes firmware */
static port_cond_t   sim_cv_host;   /* wakes host     */
static port_cond_t   sim_cv_per;    /* wakes periph   */

static uint64_t sim_granted;      /* loop iterations the host has allowed      */
static uint64_t sim_arrivals;     /* times firmware reached WDOG_Feed()        */
static int sim_parked, sim_started, sim_fw_exited;

/* Virtual clock, counted in core clock cycles. */
uint32_t SystemCoreClock = 48000000u;  /* KE04 default: 48 MHz */
static uint64_t sim_cycles;            /* cycles since power-on                     */
static uint32_t sim_loop_cycles = 48000u; /* cycles one main-loop pass takes (1 ms) */
static uint32_t sim_systick_period;    /* cycles between SysTicks, 0 = not running  */
static uint64_t sim_systick_next;      /* cycle count of the next SysTick           */
static uint64_t sim_epoch_cycles, sim_epoch_ns;  /* time at the last core clock change */
static uint32_t sim_timeout_ms = 2000;
static uint16_t sim_adc_in[32];   /* analog value per ADC channel (12-bit raw) */

#define E_TIMEOUT   (-1)   /* firmware didn't reach the next boundary (blocking loop?) */
#define E_EXITED    (-2)   /* fw main() returned */
#define E_RUNNING   (-3)   /* call needs the firmware parked */
#define E_STARTED   (-4)
#define E_NOTSTARTED (-5)

/* ---- Loop boundary: called by firmware once per superloop pass ---------- */
void WDOG_Feed(void)
{
    port_mutex_lock(&sim_mtx);
    sim_arrivals++;
    sim_parked = 1;
    port_cond_broadcast(&sim_cv_host);
    while (sim_arrivals > sim_granted)
        port_cond_wait(&sim_cv_fw, &sim_mtx);
    sim_parked = 0;
    port_cond_broadcast(&sim_cv_per);
    port_mutex_unlock(&sim_mtx);
}

/* ---- Peripheral model ---------------------------------------------------- */
static void adc_service(void)
{
    uint32_t sc1 = sim_ADC.SC1;
    uint32_t ch  = sc1 & ADC_SC1_ADCH_MASK;
    if (ch != 0x1F && !(sc1 & ADC_SC1_COCO_MASK)) {
        sim_ADC.R = sim_adc_in[ch];
        /* only flag completion if firmware hasn't started a new conversion meanwhile */
        uint32_t expected = sc1;
        __atomic_compare_exchange_n((uint32_t *)&sim_ADC.SC1, &expected,
                                    sc1 | ADC_SC1_COCO_MASK, 0,
                                    __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    }
}

static void *periph_main(void *arg)
{
    (void)arg;
    for (;;) {
        port_mutex_lock(&sim_mtx);
        while (sim_parked || !sim_started)
            port_cond_wait(&sim_cv_per, &sim_mtx);
        port_mutex_unlock(&sim_mtx);
        adc_service();
        port_yield();
    }
    return NULL;
}

static void *fw_entry(void *arg)
{
    (void)arg;
    fw_main();
    port_mutex_lock(&sim_mtx);
    sim_fw_exited = 1;
    port_cond_broadcast(&sim_cv_host);
    port_mutex_unlock(&sim_mtx);
    return NULL;
}

/* host side: wait (mutex held) until firmware is parked at the expected boundary */
static int wait_parked_locked(void)
{
    uint64_t deadline = port_now_ms() + sim_timeout_ms;

    while (!(sim_parked && sim_arrivals == sim_granted + 1)) {
        if (sim_fw_exited) return E_EXITED;
        if (port_cond_wait_until(&sim_cv_host, &sim_mtx, deadline)
            && !(sim_parked && sim_arrivals == sim_granted + 1))
            return E_TIMEOUT;
    }
    return 0;
}

/* ---- Host API ------------------------------------------------------------ */
API int sim_start(void)
{
    int r;
    if (sim_started) return E_STARTED;
    /* Win32 critical sections have no static initializer, so init here.
     * Only the host thread exists at this point. */
    port_mutex_init(&sim_mtx);
    port_cond_init(&sim_cv_fw);
    port_cond_init(&sim_cv_host);
    port_cond_init(&sim_cv_per);
    sim_started = 1;

    port_thread_create(&sim_per_th, periph_main, NULL);
    port_thread_create(&sim_fw_th,  fw_entry,    NULL);

    port_mutex_lock(&sim_mtx);
    port_cond_broadcast(&sim_cv_per);
    r = wait_parked_locked();          /* init code runs up to the first boundary */
    port_mutex_unlock(&sim_mtx);
    return r;
}

/* SysTick_Config() from the firmware: interrupt every `ticks` core cycles */
uint32_t SysTick_Config(uint32_t ticks)
{
    if (ticks == 0 || ticks > 0x01000000u) return 1;   /* 24-bit reload, as on target */
    sim_systick_period = ticks;
    sim_systick_next   = sim_cycles + ticks;
    return 0;
}

/* move the virtual clock forward, firing every SysTick that falls due
 * (mutex held, firmware parked) */
static void advance_locked(uint64_t cycles)
{
    uint64_t end = sim_cycles + cycles;
    while (sim_systick_period && sim_systick_next <= end) {
        sim_cycles = sim_systick_next;
        sim_systick_next += sim_systick_period;
        SysTick_Handler();
    }
    sim_cycles = end;
}

static uint64_t ms_to_cycles(uint64_t ms) { return ms * SystemCoreClock / 1000u; }

/* one loop pass: the pass's time elapses (ISRs fire), then the loop body runs */
static int pass_locked(uint64_t cycles)
{
    advance_locked(cycles);
    sim_granted++;
    port_cond_broadcast(&sim_cv_fw);
    return wait_parked_locked();
}

/* run n superloop iterations; each one takes sim_loop_cycles of virtual time */
API int sim_step(uint32_t n)
{
    int r = 0;
    if (!sim_started) return E_NOTSTARTED;
    port_mutex_lock(&sim_mtx);
    if (!sim_parked) r = E_RUNNING;
    for (uint32_t i = 0; i < n && r == 0; i++)
        r = pass_locked(sim_loop_cycles);
    port_mutex_unlock(&sim_mtx);
    return r;
}

/* advance the virtual clock by ms without running the loop (firmware must be parked) */
API int sim_tick(uint32_t ms)
{
    if (!sim_started) return E_NOTSTARTED;
    port_mutex_lock(&sim_mtx);
    if (!sim_parked) { port_mutex_unlock(&sim_mtx); return E_RUNNING; }
    advance_locked(ms_to_cycles(ms));
    port_mutex_unlock(&sim_mtx);
    return 0;
}

/* run for `cycles` of virtual time, one loop pass every `per_loop` cycles
 * (the last pass is shortened so the clock ends exactly on target) */
static int run_cycles(uint64_t cycles, uint64_t per_loop)
{
    int r = 0;
    if (!sim_started) return E_NOTSTARTED;
    if (per_loop == 0) per_loop = 1;
    port_mutex_lock(&sim_mtx);
    if (!sim_parked) r = E_RUNNING;
    while (cycles && r == 0) {
        uint64_t dt = cycles < per_loop ? cycles : per_loop;
        r = pass_locked(dt);
        cycles -= dt;
    }
    port_mutex_unlock(&sim_mtx);
    return r;
}

/* run ms of virtual time at the real loop rate (one pass per sim_loop_cycles) */
API int sim_run(uint32_t ms)
{
    return run_cycles(ms_to_cycles(ms), sim_loop_cycles);
}

/* fast-forward: one loop pass per ms_per_loop ms (SysTick still fires on time) */
API int sim_run_coarse(uint32_t ms, uint32_t ms_per_loop)
{
    return run_cycles(ms_to_cycles(ms), ms_to_cycles(ms_per_loop ? ms_per_loop : 1));
}

API uint64_t sim_cycles_now(void) { return sim_cycles; }
API uint64_t sim_time_ns(void)
{
    return sim_epoch_ns + (sim_cycles - sim_epoch_cycles) * 1000000000u / SystemCoreClock;
}
API uint64_t sim_time_us(void)    { return sim_time_ns() / 1000u; }
API uint64_t sim_time_ms(void)    { return sim_time_ns() / 1000000u; }
API uint64_t sim_loop_count(void) { return sim_granted; }

API uint32_t sim_get_core_clock(void)        { return SystemCoreClock; }
API void     sim_set_core_clock(uint32_t hz)
{
    if (!hz) return;
    sim_epoch_ns = sim_time_ns();       /* elapsed time so far stays as it was */
    sim_epoch_cycles = sim_cycles;
    SystemCoreClock = hz;
}
API uint32_t sim_get_loop_cycles(void)       { return sim_loop_cycles; }
API void     sim_set_loop_cycles(uint32_t c) { sim_loop_cycles = c ? c : 1; }
API uint32_t sim_get_systick_period(void)    { return sim_systick_period; }
API void     sim_set_timeout_ms(uint32_t ms) { sim_timeout_ms = ms; }

API void sim_set_adc(uint32_t ch, uint32_t raw)
{
    if (ch < 32) sim_adc_in[ch] = (uint16_t)(raw & 0x0FFF);
}

static GPIO_Type *port(uint32_t p)
{
    return p == 0 ? &sim_GPIOA : p == 1 ? &sim_GPIOB : p == 2 ? &sim_GPIOC : 0;
}

API void sim_set_gpio_in(uint32_t p, uint32_t mask, uint32_t value)
{
    GPIO_Type *g = port(p);
    if (g) g->PDIR = (g->PDIR & ~mask) | (value & mask);
}

API uint32_t sim_get_gpio_out(uint32_t p)
{
    GPIO_Type *g = port(p);
    return g ? (g->PDOR & g->PDDR) : 0;
}

API uint32_t sim_get_gpio_in(uint32_t p)
{
    GPIO_Type *g = port(p);
    return g ? g->PDIR : 0;
}
