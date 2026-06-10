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

> NOTE: This is a KVM guest with NO cpufreq driver and NO cpuidle states (see
> env_asshipped.txt). The old cpupower knobs below do NOT exist. We probe indirectly.
> Capture EVERYTHING with `2>&1 | tee` — lat_pipe prints to stderr.

`LP=/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_pipe`

One `perf stat` decomposes both suspects at once:
- **F (host frequency):** the `GHz` reading (cycles / task-clock). If load vs no-load GHz is
  ~equal, frequency did not change → F is OUT.
- **I (HLT idle / wakeup wait):** the gap between wall time and CPU time — perf's
  `seconds time elapsed` vs `task-clock` / `CPUs utilized`. Lots of wall time with little CPU
  time = the process spent the round-trip waiting (the wakeup/halt cost). If that wait shrinks
  under load, I is IN.

### Probe 1 — perf stat decomposition (NO load)
```
perf stat -e task-clock,cycles,instructions,ref-cycles,context-switches,cpu-migrations \
  $LP 2>&1 | tee task1/p2_perfstat_noload.txt
```
### Probe 2 — perf stat decomposition (WITH load)
```
stress-ng --cpu 3 --timeout 30s >/dev/null 2>&1 & sleep 1
perf stat -e task-clock,cycles,instructions,ref-cycles,context-switches,cpu-migrations \
  $LP 2>&1 | tee task1/p2_perfstat_load.txt
wait
```
### Probe 3 — pin BOTH pipe ends to one core, NO load (core can never go idle)
```
for i in $(seq 5); do taskset -c 0 $LP; done 2>&1 | tee task1/p2_pin1core_noload.txt
```
Interpretation (for the analyst, not the runner): if pin-to-one-core reproduces the ~5 µs
speedup with no bg load, idle-removal is sufficient — but it also makes the core 100% busy,
so the host may raise frequency too; Probe 1/2's GHz reading disambiguates which it was.

Then `./scripts/vmsync "task1 p2 perfstat + pin"`, report the three files, and STOP.
If the PMU is unavailable in the guest, `cycles`/`instructions` show `<not supported>` —
report that; the software `task-clock` / elapsed / CPUs-utilized fields still decompose I.

## Phase 4 — Kernel-source confirmation (/usr/src) — pick per the winner
- I (HLT→VMEXIT) → `arch/x86/kernel/process.c` (`default_idle`/`arch_safe_halt`), KVM guest
  halt path; how a guest HLT becomes a VM exit and the wakeup re-entry.
- F (host DVFS) → host-side; from the guest, evidence is the `perf stat` GHz delta itself.
- P (placement) → `kernel/sched/fair.c` (`select_task_rq_fair`, wake-affine).
Find the function, read it, explain how it produces the measured numbers. **Student makes the call.**
