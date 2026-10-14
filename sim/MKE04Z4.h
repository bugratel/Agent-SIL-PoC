/*
 * SIL override of the NXP KE04 device header.
 *
 * The real MKE04Z4.h maps peripherals to fixed addresses, e.g.
 *     #define GPIOA ((GPIO_Type *)0x400FF000u)
 * Here the same names point at ordinary global structs owned by the simulator,
 * so firmware that includes "MKE04Z4.h" compiles unchanged on the PC and the
 * host (Python) can read/write every peripheral register.
 *
 * Only the registers this PoC needs are modelled (GPIO, ADC, SysTick stubs).
 */
#ifndef MKE04Z4_SIM_H
#define MKE04Z4_SIM_H

#include <stdint.h>

#define __IO volatile
#define __I  volatile   /* not const: the simulator must be able to write inputs */
#define __O  volatile

/* ---- GPIO (KE04: GPIOA = ports A..D, 8 bits each) ---------------------- */
typedef struct {
    __IO uint32_t PDOR;  /* data output        */
    __O  uint32_t PSOR;  /* set output (W)     - see README: write side effects */
    __O  uint32_t PCOR;  /* clear output (W)   */
    __O  uint32_t PTOR;  /* toggle output (W)  */
    __I  uint32_t PDIR;  /* data input         - driven by the host */
    __IO uint32_t PDDR;  /* direction, 1 = out */
    __IO uint32_t PIDR;  /* input disable      */
} GPIO_Type;

/* ---- ADC ----------------------------------------------------------------- */
typedef struct {
    __IO uint32_t SC1;   /* COCO[7], AIEN[6], ADCO[5], ADCH[4:0] */
    __IO uint32_t SC2;
    __IO uint32_t SC3;
    __IO uint32_t SC4;
    __I  uint32_t R;     /* result - filled by the simulator's ADC model */
    __IO uint32_t CV;
    __IO uint32_t APCTL1;
    __IO uint32_t SC5;
} ADC_Type;

#define ADC_SC1_ADCH_MASK   0x1Fu
#define ADC_SC1_ADCH(x)     (((uint32_t)(x)) & ADC_SC1_ADCH_MASK)
#define ADC_SC1_COCO_MASK   0x80u

/* ---- Peripheral instances: globals instead of fixed addresses ------------ */
extern GPIO_Type sim_GPIOA, sim_GPIOB, sim_GPIOC;
extern ADC_Type  sim_ADC;

#define GPIOA (&sim_GPIOA)
#define GPIOB (&sim_GPIOB)
#define GPIOC (&sim_GPIOC)
#define ADC   (&sim_ADC)

/* ---- Core / CMSIS stubs ---------------------------------------------------
 * In the SIL, interrupts only fire while the main loop is parked at a step
 * boundary, so critical sections are trivially safe and can be no-ops. */
/* Core clock in Hz (CMSIS name). Owned by the simulator; the host can change it. */
extern uint32_t SystemCoreClock;

/* Like CMSIS: SysTick interrupt every `ticks` core clock cycles. The simulator
 * records the period and fires SysTick_Handler() from the virtual cycle counter. */
uint32_t SysTick_Config(uint32_t ticks);
#define __enable_irq()  ((void)0)
#define __disable_irq() ((void)0)
#define __WFI()         ((void)0)

/* Watchdog refresh. On target this comes from the WDOG driver; in the SIL the
 * simulator provides it and uses it as the "one main-loop iteration" boundary.
 * Superloops almost always kick the dog once per pass, so no firmware edits. */
void WDOG_Feed(void);

#endif /* MKE04Z4_SIM_H */
