# Task 1: Pipe latency under background load

## The phenomenon

`lat_pipe` measures the round-trip latency of a Unix pipe. On its own it reports about 11 µs.
When I run `stress-ng --cpu 3` in the background, it drops to about 5 µs, so adding CPU load
makes it roughly 2x faster, which is the opposite of what I expected.

Baseline, 10 runs each, median:

| | latency |
|---|---|
| no load | 11.1 µs |
| with load (stress-ng --cpu 3) | 5.3 µs |

The effect is much bigger than the run-to-run spread, so it is real.

## What could cause it

The benchmark does a fixed amount of work per round-trip, so the latency is basically
(work / CPU speed) + (cost of waking up the other process). Background load could help in three
ways: the CPU could run at a higher clock under load (frequency), an idle CPU could be paying a
sleep/wakeup cost (idle states), or the two pipe processes could be placed differently by the
scheduler (placement).

This is a KVM guest with no cpufreq or cpuidle drivers exposed (I checked with cpupower), so I
could not toggle frequency or C-states directly. I probed indirectly with perf instead.

## Ruling out frequency

I compared `cycles` to `ref-cycles` in perf stat. ref-cycles count at the fixed nominal clock,
so their ratio is the real frequency. It was the same (1.35) with and without load, which means
that when the CPU is actually running it runs at the same speed either way. Frequency is not it.

## Separating idle from placement

Pinning both pipe processes to a single core (no load) gave 5.4 µs, as fast as the loaded case.
But that changes two things at once (the core can no longer go idle, and both ends are now on the
same core), so it does not tell me which one matters.

To separate them I pinned both ends to two cores and ran it twice: once letting the cores idle,
once with a busy spinner on each core so they never idle.

| | latency |
|---|---|
| same core | 5.4 µs |
| two cores, idle | 11.2 µs |
| two cores, kept busy | 10.9 µs |

Keeping the cores busy did not help, so idle/halt cost is not the cause. The thing that matters
is same-core versus cross-core.

## The cause: scheduler placement

When there are idle CPUs, the scheduler spreads the two pipe processes onto different cores, so
every round-trip is a cross-core wakeup (an inter-processor interrupt, plus the pipe buffer and
task data bouncing between the two cores' caches). Background load keeps all the CPUs busy, so the
scheduler puts both processes on the same core, where a context switch is about twice as cheap.

Two pieces of evidence back this up. A perf sched callgraph of the no-load run shows the two pipe
processes on different cores (cpu0 and cpu3), each handing the CPU to the idle task when it blocks.
And an off-CPU latency histogram shows the per-handoff wait dropping from the 8-16 µs bucket
(no load) to the 2-4 µs bucket (with load), which matches the latency change.

## Kernel source

The placement decision is in `kernel/sched/fair.c`. On a wakeup, `select_task_rq_fair` takes the
fast path to `select_idle_sibling`, and inside it (line 110) `select_idle_cpu` scans the cache
domain for an idle CPU and returns it. So with idle CPUs available the woken pipe process is sent
to a free core, away from its partner. Under load there is no idle CPU to find, so it stays on the
waker's core and the two processes run together. That co-location is why load makes the pipe faster.
