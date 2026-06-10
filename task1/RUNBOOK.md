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

## Phase 2B — Isolate idle (I) from placement (P), + direct wakeup latency
`LP=/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_pipe`

### Probe 4 — cross-core, cores allowed to go IDLE (pipe on cpus 0,1, no load)
```
for i in $(seq 5); do taskset -c 0,1 $LP; done 2>&1 | tee task1/p2b_pin2core_idle.txt
```
### Probe 5 — cross-core, cores kept BUSY (one spinner pinned to each of 0,1)
Same placement as Probe 4; the ONLY change is the cores never idle.
```
taskset -c 0 stress-ng --cpu 1 --timeout 40s >/dev/null 2>&1 &
taskset -c 1 stress-ng --cpu 1 --timeout 40s >/dev/null 2>&1 &
sleep 1
for i in $(seq 5); do taskset -c 0,1 $LP; done 2>&1 | tee task1/p2b_pin2core_busy.txt
wait
```
Decision: Probe4 slow (~11) + Probe5 fast (~5) ⇒ idle is the cause, placement held constant
⇒ **P ruled out, I ruled in.** If Probe5 is also ~11, idle is NOT sufficient and P is back in.

### Probe 6 — direct wakeup latency + actual CPU placement (no load vs load)
```
# no load:
perf sched record -o p2b_sched_noload.data -- bash -c 'for i in $(seq 3); do '"$LP"'; done' 2>&1 | tail -2
perf sched latency  -i p2b_sched_noload.data 2>&1 | tee task1/p2b_sched_latency_noload.txt
perf sched timehist -i p2b_sched_noload.data 2>&1 | head -40 | tee task1/p2b_sched_timehist_noload.txt
# with load:
stress-ng --cpu 3 --timeout 40s >/dev/null 2>&1 & sleep 1
perf sched record -o p2b_sched_load.data -- bash -c 'for i in $(seq 3); do '"$LP"'; done' 2>&1 | tail -2
perf sched latency  -i p2b_sched_load.data 2>&1 | tee task1/p2b_sched_latency_load.txt
perf sched timehist -i p2b_sched_load.data 2>&1 | head -40 | tee task1/p2b_sched_timehist_load.txt
wait
```
This gives the wakeup→run delay (direct fingerprint of I) AND the CPU column shows whether the
pipe ends are actually cross-core under load (tests the assumption, doesn't assume it).
Then `./scripts/vmsync "task1 p2b"`, report the files, STOP.

## Phase 4 — Confirm P in kernel source + produce deck-sanctioned evidence
Winner is **P (scheduler wake-placement)**. Two things the lecturer requires that we still owe:
(a) confirmation in `/usr/src`, (b) a histogram and a sampled callgraph (assignment text;
deck slides 32, 66, 69–70).

### Probe 7 — locate the placement logic in /usr/src (mechanical; student interprets)
```
ls /usr/src
F=$(ls -d /usr/src/linux-* 2>/dev/null | head -1); echo "src=$F"
grep -n 'select_idle_sibling\|wake_affine\|select_task_rq_fair' $F/kernel/sched/fair.c | head
# dump the two functions for reading:
sed -n '/^static int\s*$/,/^}/p' /dev/null  # (placeholder)
awk '/select_idle_sibling\(struct task_struct/{p=1} p{print} /^}/{if(p)exit}' $F/kernel/sched/fair.c > task1/p4_select_idle_sibling.txt
awk '/select_task_rq_fair\(/{p=1} p{print} /^}/{if(p)exit}'                $F/kernel/sched/fair.c > task1/p4_select_task_rq_fair.txt
wc -l task1/p4_select_*.txt
```
(If awk ranges miss, just `grep -n` the function start lines and `sed -n 'START,+120p'` to dump.)

### Probe 8 — sampled callgraph of the wakeup/switch path (deck's scheduler recipe, slide 69)
```
LP=/usr/lib/lmbench/bin/x86_64-linux-gnu/lat_pipe
sudo perf record -e sched:sched_switch -g -o task1/p4_cs_noload.data -- \
  bash -c 'for i in 1 2 3; do '"$LP"'; done' 2>&1 | tail -2
sudo perf report -i task1/p4_cs_noload.data --stdio 2>/dev/null | head -80 \
  | tee task1/p4_callgraph_noload.txt
sudo chown ubuntu task1/p4_cs_noload.data task1/p4_callgraph_noload.txt
# keep the .data only if small (<50M); else delete after capturing the report.
```
We want to SEE the no-load wakeup go through `try_to_wake_up → select_task_rq → ttwu_queue`
(remote/cross-CPU), i.e. the placement machinery, in the callgraph.

### Probe 9 — off-CPU latency histogram, no-load vs load (the assignment's "histogram", slide 32)
Off-CPU time per pipe block = sched_switch(out, blocked) → sched_switch(in). It contains the
wakeup cost; its distribution should shift between conditions.
```
BT='tracepoint:sched:sched_switch /args->prev_comm=="lat_pipe" && args->prev_state/ { @t[args->prev_pid]=nsecs; }
tracepoint:sched:sched_switch /args->next_comm=="lat_pipe"/ { $s=@t[args->next_pid]; if($s){ @off_us=hist((nsecs-$s)/1000); delete(@t[args->next_pid]); } }'
# no load (run ~8s while lat_pipe runs in another shell, or wrap):
sudo bpftrace -e "$BT" -c "$LP" 2>&1 | tee task1/p4_offcpu_hist_noload.txt
# with load:
stress-ng --cpu 3 --timeout 20s >/dev/null 2>&1 & sleep 1
sudo bpftrace -e "$BT" -c "$LP" 2>&1 | tee task1/p4_offcpu_hist_load.txt
wait
```
Then `./scripts/vmsync "task1 p4 source + callgraph + histogram"`, report the files, STOP.
Note for writeup: governor/idle pinning (deck slide 65) is N/A here — the guest exposes no
cpufreq/cpuidle; we instead confirmed frequency was constant via cycles/ref-cycles.
