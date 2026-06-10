# Task 1 — Why pipe latency *improves* under background CPU load

**Phenomenon.** `lat_pipe` (lmbench "hot-potato" Unix-pipe round-trip) reports ~11 µs idle,
but ~5 µs when `stress-ng --cpu 3` runs alongside it — a ~2× *speed-up* from adding load.

**Result (one line).** The cause is **scheduler wake-placement**, not frequency or idle/halt
cost. When idle CPUs exist, the scheduler spreads the two pipe partners onto *different* cores,
so every round-trip pays a cross-core wakeup (reschedule IPI + cache-line bounce). Background
load removes the idle CPUs, so the scheduler co-locates the pair on one core, turning each
round-trip into a cheap same-core context switch. Confirmed in `kernel/sched/fair.c`,
`select_idle_sibling()` line 110 (`select_idle_cpu`).

---

## 1. Environment (as shipped — recorded before changing anything)
`task1/env_asshipped.txt`

- **KVM full-virt guest** (`systemd-detect-virt = kvm`), Intel Xeon Gold 5420+, **4 vCPUs**,
  1 thread/core, each vCPU presented as its own socket; 1 NUMA node. Kernel 7.0.0-15-generic.
- **No cpufreq driver** in the guest (`cpupower frequency-info`: "no or unknown cpufreq driver
  active"; governors Not Available). `cpu MHz` fixed at 2000.
- **No cpuidle states** (`cpupower idle-info`: driver `none`, "No idle states").
- `perf_event_paranoid = -1` (full perf access incl. kernel).

**Methodological note (lecturer slide 65 vs our setup).** The deck recommends pinning the
governor to `performance` and disabling idle states for a stable benchmark. Those knobs **do
not exist inside this guest**, so we instead (a) recorded the environment as shipped, (b)
reproduced the effect, then (c) controlled one variable at a time, and verified frequency was
constant *via counters* rather than by pinning. This is a reasoned deviation, not an oversight.

## 2. Baseline + variance
`logs/task1_20260610_1427.log` (lat_pipe prints to stderr; numbers are from the session log)

| condition | n | median | min–max |
|---|---|---|---|
| no load | 10 | **11.12 µs** | 9.88 – 11.20 |
| `stress-ng --cpu 3` | 10 | **5.26 µs** | 4.74 – 6.39 |

Effect = **2.1× (5.9 µs)**, far larger than the run-to-run spread. The effect is real and stable.

## 3. Three hypotheses, and how each was settled

We compared the good (loaded, fast) and bad (idle, slow) cases and drilled down from counters
to controlled experiments to source.

### F — CPU frequency scaling (DVFS). **RULED OUT.**
`task1/p2_perfstat_{noload,load}.txt`

`perf stat` frequency, from `cycles ÷ ref-cycles` (a ratio, so immune to the fact that
`lat_pipe` self-calibrated to different iteration counts under contention):

| | cycles | ref-cycles | ratio = freq/nominal |
|---|---|---|---|
| no load | 1.620 B | 1.203 B | **1.347** |
| with load | 73.485 B | 54.567 B | **1.347** |

Identical → when the core actually executes, it runs at the same ~2.7 GHz in both conditions.
Frequency does not change. **F is out.** (The raw `perf stat` latency of ~69 µs is measurement
overhead and is *not* compared across runs — only the ratio is.)

### I — Idle / HLT→VMEXIT wakeup cost. **RULED OUT.**
`task1/p2b_pin2core_idle.txt`, `task1/p2b_pin2core_busy.txt`

Initial (wrong) reading: pinning *both* pipe ends to one core reproduced the speed-up
(`p2_pin1core_noload.txt`: 5.42 µs, no load) — suggesting idle removal was the cause. But that
probe changed **two** variables at once (no-idle *and* same-core). A placement-controlled
experiment (Phase 2B) fixed placement = cross-core and flipped only idle:

| placement | cores idle between turns? | latency |
|---|---|---|
| cross-core (cpus 0,1) | yes | 11.2 µs |
| cross-core (cpus 0,1) + a spinner pinned to each | **no** | 10.9 µs |

Keeping the cores busy with placement held cross-core did **not** help. **I is out.**
(Confound handled: the no-spinner case (no contention) and the spinner case (contention) are
*both* ~11 µs, so the slowness is the cross-core placement, not CPU contention.)

### P — Scheduler wake-placement. **RULED IN.**
`task1/p2_pin1core_noload.txt` + Phase 2B + Phase 4 evidence

Holding the *other* variable (idle) fixed and flipping placement:

| placement | core state | latency |
|---|---|---|
| **same core** (pin 1) | busy | **5.4 µs** |
| cross core (pin 2, idle or busy) | either | ~11 µs |
| loaded, unpinned (scheduler chose) | busy | 5.3 µs |
| idle, unpinned (scheduler chose) | idle | 11.1 µs |

Same-core ⇒ fast, cross-core ⇒ slow, independent of idle/busy. **Placement is the axis.**

## 4. Direct evidence (histogram + sampled callgraph)

**Off-CPU latency histogram** (bpftrace on `sched_switch`) — `task1/p4_offcpu_hist_*.txt`.
Time each pipe end stays blocked per handoff:

```
no load:   mode [8, 16) µs   (139,094 hits)      with load:  mode [2, 4) µs  (311,579 hits)
           [16,32) µs 33,289                                  [4, 8) µs  40,707
```
The whole distribution drops ~4×, matching the 11→5 µs latency.

**Sampled callgraph** (`perf record -e sched:sched_switch -g`) — `task1/p4_callgraph_noload.txt`:
```
16.63%  lat_pipe S ==> swapper/0:0     ← end #1 blocks; cpu0 goes idle
14.74%  lat_pipe S ==> swapper/3:0     ← end #2 blocks; cpu3 goes idle
        anon_pipe_read → schedule → __schedule
```
No-load, the two ends sit on **separate cores (0 and 3)** and each blocks to the idle task
(`swapper`) — i.e. the pair is spread, and every resume is a cross-core wakeup. This is the
mechanism made visible.

*(The per-CPU switch-count files `p2b_latpipe_cpudist_*` are aggregated over many iterations and
are inconclusive about momentary co-location; treated as weak supporting data only.)*

## 5. Kernel-source confirmation — `kernel/sched/fair.c`
`task1/p4_select_task_rq_fair.txt` (fair.c:8579), `task1/p4_select_idle_sibling.txt` (fair.c:7836)

Wakeup placement path: `try_to_wake_up → select_task_rq → select_task_rq_fair`. For a normal
task wakeup it takes the **fast path**, `select_task_rq_fair` line 64:
```c
new_cpu = select_idle_sibling(p, prev_cpu, new_cpu);
```
Inside `select_idle_sibling`, the early returns use `target`/`prev` *only if already idle*
(lines 24–26, 31–37). The decisive step is **line 110**:
```c
i = select_idle_cpu(p, sd, has_idle_core, target);   /* scan LLC domain for ANY idle CPU */
if ((unsigned)i < nr_cpumask_bits)
        return i;
```
- **Idle system (no bg load):** `select_idle_cpu` finds an idle CPU and returns it → the woken
  pipe partner is placed on a *different* core than the waker → the pair is split across cores →
  each hot-potato round-trip is a cross-core wakeup (reschedule IPI + pipe-buffer/`task_struct`
  cache-line bounce between cores) ≈ **11 µs**.
- **Loaded system:** `select_idle_cpu` finds **no** idle CPU (falls past line 111) → the function
  returns `prev`/`target` → the partner is **co-located with the waker** → same-core context
  switch, no IPI, no cross-core cache traffic ≈ **5 µs**.

So background load helps **by accident**: it removes the idle CPUs that `select_idle_cpu` would
otherwise hand the wakee, forcing co-location.

## 6. Conclusion
The pipe benchmark is faster under CPU load because the Linux scheduler's idle-CPU-seeking
wakeup placement (`select_idle_sibling`/`select_idle_cpu`) **spreads** the two communicating
processes across cores when CPUs are idle, making every round-trip a cross-core wakeup; load
removes the idle CPUs and forces same-core co-location, which is ~2× cheaper. Frequency scaling
(F) was ruled out by a constant `cycles/ref-cycles` ratio; idle/HLT cost (I) was ruled out
because cross-core-but-busy stayed slow. The mechanism was confirmed by a placement-controlled
experiment that toggles the effect, an off-CPU latency histogram, a sampled callgraph, and the
kernel source.

## Evidence index
| File | Evidence |
|---|---|
| `task1/env_asshipped.txt` | environment (KVM, no cpufreq/cpuidle) |
| `logs/task1_20260610_1427.log` | baseline ×10 each condition |
| `task1/p2_perfstat_{noload,load}.txt` | F ruled out (freq ratio constant) |
| `task1/p2_pin1core_noload.txt` | same-core = fast |
| `task1/p2b_pin2core_{idle,busy}.txt` | I ruled out (cross-core busy still slow) |
| `task1/p4_offcpu_hist_{noload,load}.txt` | off-CPU latency histogram |
| `task1/p4_callgraph_noload.txt` | sampled callgraph (pair on separate cores → swapper) |
| `task1/p4_select_idle_sibling.txt`, `p4_select_task_rq_fair.txt` | kernel source |
