# Task 1 Runbook — Pipe latency improves under background CPU load

> Run everything on `course-08` under a session log:
> `script -f ~/hw1/logs/task1_$(date +%Y%m%d_%H%M).log`
> Save each tool's raw output as its own file in this dir (named by what it tested).
> **PREDICT in writeup/notes.md before every measured run.** No peeking at results first.

`LP=/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_pipe`

---

## Phase 0 — Environment as shipped (capture BEFORE changing anything)
```
uname -a
systemd-detect-virt                      # are we a KVM guest? changes how C-states appear
nproc; lscpu
cat /proc/cpuinfo | grep -E 'model name|MHz' | head
cpupower frequency-info                   # governor + driver (intel_pstate? acpi-cpufreq?)
cpupower idle-info                        # which C-states exist + exit latencies
cat /sys/devices/system/cpu/cpu0/cpuidle/state*/name 2>/dev/null
cat /proc/sys/kernel/perf_event_paranoid
ls -la ~                                  # homework binaries/sources
```
Save as `task1/env_asshipped.txt`. → fill the Environment block in notes.md.

## Phase 1 — Baseline + variance (establish the effect AND the noise floor)
```
# no load, n=10
for i in $(seq 10); do $LP; done | tee baseline_noload.txt
# with load, n=10  (3 hogs on a 4-core box)
for i in $(seq 10); do stress-ng --cpu 3 --timeout 15s >/dev/null 2>&1 & sleep 1; $LP; wait; done | tee baseline_load.txt
```
Report median / min / spread for each. Effect must be >> run-to-run noise before continuing.

## Phase 2 — Discriminating probes (the three competing mechanisms)

The instruction count barely moves; cycles and wakeup latency are the question.

### H1 — CPU frequency scaling (DVFS)
Prediction: under no load the governor keeps freq low; load pins it high.
```
# instructions ~constant, cycles differ?  compare the two conditions:
perf stat -e cycles,instructions,task-clock $LP                    # no load
stress-ng --cpu 3 --timeout 20s & sleep 1; perf stat -e cycles,instructions,task-clock $LP; wait
turbostat --quiet -- $LP                                           # Bzy_MHz / Avg_MHz, no load
stress-ng --cpu 3 --timeout 20s & sleep 1; turbostat --quiet -- $LP; wait
```
Knob test (one variable): force max freq, NO load. If latency drops to the loaded value → DVFS confirmed.
```
sudo cpupower frequency-set -g performance
for i in $(seq 10); do $LP; done | tee h1_perfgov_noload.txt
sudo cpupower frequency-set -g <original-governor>     # restore!
```

### H2 — Idle states / halt-exit latency (C-states; in a guest = HLT VM-exit)
Prediction: idle CPU enters a deep state; partner wakeup pays exit latency. Load keeps a CPU hot.
```
turbostat --quiet -- $LP        # look at C-state residency columns (CPU%c1/c6, etc.), no load vs load
# knob test: disable deep idle, NO load. latency drops? -> idle-exit latency confirmed.
sudo cpupower idle-set -D 0     # disable all but shallowest; (or idle-set -d <N> per state)
for i in $(seq 10); do $LP; done | tee h2_noidle_noload.txt
sudo cpupower idle-set -E       # re-enable all
```

### H3 — Scheduler placement / wakeup migration
Prediction: unloaded, the two pipe ends bounce across idle CPUs (cold cache, cross-CPU IPI);
under load they get concentrated/co-scheduled (warm).
```
perf stat -e context-switches,cpu-migrations $LP                  # no load
stress-ng --cpu 3 --timeout 20s & sleep 1; perf stat -e context-switches,cpu-migrations $LP; wait
# ftrace the wakeups/switches for one run:
sudo trace-cmd record -e sched:sched_wakeup -e sched:sched_switch -e sched:sched_migrate_task $LP
sudo trace-cmd report | tee h3_sched_noload.txt
# knob test: pin everything to ONE cpu, no load. effect reproduce?
taskset -c 0 bash -c "for i in \$(seq 10); do $LP; done" | tee h3_pinned_noload.txt
```

## Phase 4 — Kernel-source confirmation (/usr/src)
Whichever knob reproduced the loaded latency points you to the subsystem:
- DVFS → `drivers/cpufreq/` (e.g. `intel_pstate.c`) + the schedutil path `kernel/sched/cpufreq_schedutil.c`
- idle  → `drivers/cpuidle/` + `drivers/acpi/processor_idle.c`; in a guest look at `cpuidle-haltpoll`
- sched → `kernel/sched/fair.c` (`select_task_rq_fair`, wake-affine)

Find the function, read what it does, explain how it produces the measured numbers. **You make the final connection.**
