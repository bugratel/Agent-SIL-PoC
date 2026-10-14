/*
 * Example non-RTOS fridge controller (stand-in for the real Beko firmware).
 * Plain superloop + SysTick, written the way it would be for the KE04 target.
 * Nothing in here knows it is running in a simulator.
 */
#include <stdint.h>
#include "MKE04Z4.h"

/* ---- Pins (KE04 GPIOA: bits 0-7 = PTA, bits 8-15 = PTB) ------------------- */
#define PIN_COMP    (1u << 0)    /* PTA0  compressor relay    */
#define PIN_LIGHT   (1u << 1)    /* PTA1  cabin light         */
#define PIN_BUZZER  (1u << 2)    /* PTA2  door-open buzzer    */
#define PIN_DOOR    (1u << 8)    /* PTB0  door switch, 1=open */

#define NTC_CH            0u
#define COMP_MIN_OFF_MS   (3u * 60u * 1000u)   /* compressor restart protection */
#define DOOR_ALARM_MS     (60u * 1000u)

typedef enum { ST_IDLE = 0, ST_WAIT_PROTECT = 1, ST_COOLING = 2 } fridge_state_t;

/* ---- Globals ------------------------------------------------------------- */
volatile uint32_t g_ms;              /* SysTick ms counter              */
int16_t g_setpoint_c10 = 40;         /* target temp, tenths of C (4.0 C) */
int16_t g_hyst_c10     = 10;         /* band around setpoint (+/-1.0 C) */
int16_t g_cabin_c10;                 /* measured cabin temp, tenths of C */

static fridge_state_t s_state = ST_IDLE;  /* thermostat state machine       */
static uint32_t s_comp_off_at;       /* g_ms when compressor last stopped */
static uint32_t s_door_open_at;      /* g_ms when door was opened       */
static uint8_t  s_door_was_open;     /* door state on the previous pass */
static uint8_t  s_alarm;             /* 1 = door open > 60 s, buzzer on */

/* NTC 10k B3950, 10k pull-up, 12-bit ADC (placeholder until Beko's tables) */
static const struct { uint16_t raw; int16_t c10; } ntc_tab[] = {
    {3900, -300}, {3830, -250}, {3740, -200}, {3629, -150}, {3495, -100},
    {3337,  -50}, {3156,    0}, {2955,   50}, {2738,  100}, {2510,  150},
    {2278,  200}, {2048,  250}, {1825,  300}, {1614,  350}, {1419,  400},
    {1241,  450}, {1081,  500},
};
#define NTC_N (sizeof ntc_tab / sizeof ntc_tab[0])

void SysTick_Handler(void) { g_ms++; }

static uint16_t adc_read(uint32_t ch)
{
    ADC->SC1 = ADC_SC1_ADCH(ch);                     /* start conversion */
    while (!(ADC->SC1 & ADC_SC1_COCO_MASK)) { }      /* wait for COCO    */
    return (uint16_t)ADC->R;
}

static int16_t ntc_to_c10(uint16_t raw)
{
    if (raw >= ntc_tab[0].raw)         return ntc_tab[0].c10;
    if (raw <= ntc_tab[NTC_N - 1].raw) return ntc_tab[NTC_N - 1].c10;
    for (uint32_t i = 1; i < NTC_N; i++) {
        if (raw >= ntc_tab[i].raw) {
            int32_t r0 = ntc_tab[i - 1].raw, r1 = ntc_tab[i].raw;
            int32_t t0 = ntc_tab[i - 1].c10, t1 = ntc_tab[i].c10;
            return (int16_t)(t0 + (t1 - t0) * (r0 - (int32_t)raw) / (r0 - r1));
        }
    }
    return 0;
}

static void set_pin(uint32_t pin, int on)
{
    if (on) GPIOA->PDOR |= pin; else GPIOA->PDOR &= ~pin;
}

static void door_task(void)
{
    uint8_t open = (GPIOA->PDIR & PIN_DOOR) ? 1u : 0u;
    if (open && !s_door_was_open) s_door_open_at = g_ms;
    s_door_was_open = open;

    set_pin(PIN_LIGHT, open);
    s_alarm = open && (g_ms - s_door_open_at >= DOOR_ALARM_MS);
    set_pin(PIN_BUZZER, s_alarm);
}

static void thermostat_task(void)
{
    int demand = g_cabin_c10 > g_setpoint_c10 + g_hyst_c10;

    switch (s_state) {
    case ST_IDLE:
    case ST_WAIT_PROTECT:
        if (!demand) { s_state = ST_IDLE; break; }
        if (g_ms - s_comp_off_at >= COMP_MIN_OFF_MS) {
            s_state = ST_COOLING;
            set_pin(PIN_COMP, 1);
        } else {
            s_state = ST_WAIT_PROTECT;
        }
        break;
    case ST_COOLING:
        if (g_cabin_c10 <= g_setpoint_c10 - g_hyst_c10) {
            s_state = ST_IDLE;
            set_pin(PIN_COMP, 0);
            s_comp_off_at = g_ms;
        }
        break;
    }
}

int main(void)
{
    SysTick_Config(48000u);                           /* 1 ms @ 48 MHz */
    GPIOA->PDDR |= PIN_COMP | PIN_LIGHT | PIN_BUZZER;
    GPIOA->PDOR &= ~(PIN_COMP | PIN_LIGHT | PIN_BUZZER);
    ADC->APCTL1 = 1u << NTC_CH;
    s_comp_off_at = g_ms;                             /* protection also after power-up */
    __enable_irq();

    for (;;) {
        WDOG_Feed();
        g_cabin_c10 = ntc_to_c10(adc_read(NTC_CH));
        door_task();
        thermostat_task();
    }
}
