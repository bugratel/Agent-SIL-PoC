#ifndef SIM_PORT_H
#define SIM_PORT_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#  include <process.h>

#  define API __declspec(dllexport)

typedef CRITICAL_SECTION   port_mutex_t;
typedef CONDITION_VARIABLE port_cond_t;
typedef HANDLE             port_thread_t;

static inline void port_mutex_init(port_mutex_t *m)   { InitializeCriticalSection(m); }
static inline void port_mutex_lock(port_mutex_t *m)   { EnterCriticalSection(m); }
static inline void port_mutex_unlock(port_mutex_t *m) { LeaveCriticalSection(m); }
static inline void port_cond_init(port_cond_t *c)     { InitializeConditionVariable(c); }
static inline void port_cond_wait(port_cond_t *c, port_mutex_t *m)
{
    SleepConditionVariableCS(c, m, INFINITE);
}
static inline void port_cond_broadcast(port_cond_t *c) { WakeAllConditionVariable(c); }
static inline uint64_t port_now_ms(void)               { return GetTickCount64(); }
/* returns 0 if woken, 1 if the deadline (absolute, port_now_ms) passed */
static inline int port_cond_wait_until(port_cond_t *c, port_mutex_t *m, uint64_t deadline)
{
    uint64_t now = port_now_ms();
    if (now >= deadline) return 1;
    if (!SleepConditionVariableCS(c, m, (DWORD)(deadline - now)))
        return GetLastError() == ERROR_TIMEOUT ? 1 : 0;
    return 0;
}

typedef struct { void *(*fn)(void *); void *arg; } port_start_t;
static port_start_t port_starts[4];
static volatile LONG port_nstarts;
static unsigned __stdcall port_trampoline(void *p)
{
    port_start_t *s = (port_start_t *)p;
    s->fn(s->arg);
    return 0;
}
static inline int port_thread_create(port_thread_t *t, void *(*fn)(void *), void *arg)
{
    LONG i = InterlockedIncrement(&port_nstarts) - 1;
    port_starts[i].fn = fn;
    port_starts[i].arg = arg;
    *t = (HANDLE)_beginthreadex(NULL, 0, port_trampoline, &port_starts[i], 0, NULL);
    return *t ? 0 : -1;
}
static inline void port_yield(void) { SwitchToThread(); }

#else /* POSIX */
#  include <pthread.h>
#  include <sched.h>
#  include <time.h>
#  include <errno.h>

#  define API __attribute__((visibility("default")))

typedef pthread_mutex_t port_mutex_t;
typedef pthread_cond_t  port_cond_t;
typedef pthread_t       port_thread_t;

static inline void port_mutex_init(port_mutex_t *m)   { pthread_mutex_init(m, NULL); }
static inline void port_mutex_lock(port_mutex_t *m)   { pthread_mutex_lock(m); }
static inline void port_mutex_unlock(port_mutex_t *m) { pthread_mutex_unlock(m); }
static inline void port_cond_init(port_cond_t *c)     { pthread_cond_init(c, NULL); }
static inline void port_cond_wait(port_cond_t *c, port_mutex_t *m) { pthread_cond_wait(c, m); }
static inline void port_cond_broadcast(port_cond_t *c) { pthread_cond_broadcast(c); }
static inline uint64_t port_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}
static inline int port_cond_wait_until(port_cond_t *c, port_mutex_t *m, uint64_t deadline)
{
    struct timespec ts = { (time_t)(deadline / 1000u), (long)(deadline % 1000u) * 1000000L };
    return pthread_cond_timedwait(c, m, &ts) == ETIMEDOUT;
}
static inline int port_thread_create(port_thread_t *t, void *(*fn)(void *), void *arg)
{
    return pthread_create(t, NULL, fn, arg);
}
static inline void port_yield(void) { sched_yield(); }
#endif

#endif /* SIM_PORT_H */
