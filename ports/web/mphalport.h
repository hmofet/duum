/* MicroPython HAL for the Duum web port.
 *
 * py/mphal.h includes this first and then declares mp_hal_* only where the
 * port has not, so the tick helpers are defined inline here (macro-guarded so
 * mphal.h skips its own prototypes) and the rest live in port.c.
 *
 * The clock is emscripten's monotonic millisecond clock rather than the
 * device's 60 Hz TickCount, but uno.ticks() still reports 60 Hz units so the
 * engine takes its normal timing path and not its fallback one.
 */
#ifndef DUUM_WEB_MPHALPORT_H
#define DUUM_WEB_MPHALPORT_H

unsigned long duum_now_ms(void);

#define mp_hal_ticks_ms  mp_hal_ticks_ms
static inline mp_uint_t mp_hal_ticks_ms(void)  { return (mp_uint_t)duum_now_ms(); }
#define mp_hal_ticks_us  mp_hal_ticks_us
static inline mp_uint_t mp_hal_ticks_us(void)  { return (mp_uint_t)(duum_now_ms() * 1000u); }
#define mp_hal_ticks_cpu mp_hal_ticks_cpu
static inline mp_uint_t mp_hal_ticks_cpu(void) { return 0; }

static inline void mp_hal_set_interrupt_char(char c) { (void)c; }

#endif
