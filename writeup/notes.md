# HW1 — Investigation Notes

Running hypothesis log. The final writeup is assembled from this. Keep it honest:
record the hypotheses that were WRONG and how you killed them — that's most of the grade.

---

## Task 1 — Pipe latency improves under background CPU load

### Environment (as shipped, before any changes)  [evidence: task1/env_asshipped.txt]
- CPU model / cores / threads: Intel Xeon Gold 5420+ (Sapphire Rapids), 4 vCPUs, 1 thread/core, each vCPU presented as its own socket (no SMT siblings). NUMA: 1 node.
- **KVM full-virt guest** (`systemd-detect-virt = kvm`). cpu MHz pinned at 2000 for all.
- **cpufreq driver: NONE** — "no or unknown cpufreq driver active"; governors Not Available; boost not supported. ⇒ guest cannot read or set frequency; DVFS is host-side and invisible here.
- **cpuidle driver: NONE**, "No idle states"; governor menu (nothing to govern). ⇒ guest idle = default arch halt → HLT → VMEXIT to host.
- perf_event_paranoid: -1 (full perf access, incl. kernel).
- Kernel: 7.0.0-15-generic (Ubuntu, PREEMPT_DYNAMIC).

### Baseline + variance  [evidence: logs/task1_20260610_1427.log; note: lat_pipe prints to stderr, so per-file tee was empty — numbers from the script log]
- Command: `lat_pipe` ×10, no-load and with `stress-ng --cpu 3`.
- Reported as median (robust to the occasional warm-up outlier) + min–max spread.
- No-load: median **11.12 µs** (range 9.88–11.20, tight cluster ~11.1).
- With-load: median **5.26 µs** (range 4.74–6.39).
- Effect size vs noise: **2.1× / 5.9 µs gap, >> spread.** Effect is real and stable.

### Prediction (student, before Phase 2 — predict-then-measure)
- **Bet: I (HLT→VMEXIT) dominates.** Rationale: stress-ng's whole effect is keeping vCPUs
  from halting; the HLT→host round-trip is the obvious per-wakeup cost in a VM.
- Caveat on record: the student initially dismissed F as "illogical," but DVFS/turbo is
  automatic default behavior — so F is NOT dismissed by argument; it must be ruled out with
  a frequency measurement (`perf stat` GHz). If under load the measured GHz ≈ no-load GHz,
  F is out and I/P remain.
- Falsifiable form of the bet: (a) `perf stat` shows instructions/txn ~equal and GHz ~equal
  load vs no-load; (b) removing idle without bg load reproduces the speedup.

### Hypotheses — REVISED for the KVM guest (the original cpupower knobs do NOT exist here)
Phase 0 killed our planned knobs: no cpufreq driver (can't pin frequency), no cpuidle states
(can't `idle-set`). Both DVFS and idle now live at the host/virtualization boundary and must
be probed indirectly from inside the guest. Revised discriminators:

| # | Hypothesis | Prediction (counter/behavior) | In-guest discriminating measurement | Result: ruled in / out | Evidence file |
|---|-----------|-------------------------------|----------------------------|------------------------|---------------|
| F | Host DVFS: physical core runs slow when guest is idle-ish; bg load makes host raise freq | `cycles`(GHz) lower unloaded; **instructions/transaction equal**; unhalted-cycles/txn equal | `perf stat cycles,ref-cycles,instructions,task-clock` no-load vs load | **RULED OUT** — cycles/ref-cycles = 1.347 in BOTH conditions ⇒ when-running frequency identical (~2.7 GHz). Frequency does not change. | p2_perfstat_{noload,load}.txt |
| I | HLT→VMEXIT: idle reader HLTs → VMEXIT; wakeup pays VMENTER. Load = vCPUs never HLT | keeping the pipe core busy (no bg hogs), placement held constant, reproduces the speedup | **Phase 2B**: cross-core IDLE vs cross-core BUSY | **RULED OUT** — cross-core BUSY (Probe 5) = 10.9 µs ≈ cross-core IDLE (Probe 4) = 11.2 µs. Removing idle with placement fixed did NOT help. Idle/HLT is not the cause. | p2b_pin2core_{idle,busy}.txt |
| P | Scheduler placement: with idle CPUs the scheduler SPREADS the pair cross-core (each round-trip = IPI + cross-core cache bounce); load removes idle CPUs ⇒ pair co-located same-core | same-core fast, cross-core slow, independent of idle/busy; load ⇒ pair concentrated on one CPU | Phase 2B matrix + `perf sched timehist` placement | **RULED IN** — same-core (pin-1) 5.4 µs vs cross-core (pin-2, idle OR busy) ~11 µs. timehist: loaded run concentrates lat_pipe on one CPU; unloaded spreads it. | p2_pin1core_noload.txt, p2b_*, p2b_sched_timehist_*.txt |

**VERDICT (revised — earlier "I" verdict was WRONG; the placement-controlled Phase 2B overturned it):**
The mechanism is **scheduler wake-placement (P)**. When idle CPUs exist, `select_idle_sibling`
places the woken pipe partner on a *different* (idle) core → every hot-potato round-trip is a
cross-core wakeup (reschedule IPI + pipe-buffer/task-struct cache-line bounce between cores),
~11 µs. Background CPU load removes the idle CPUs, so the scheduler co-locates the pair on one
core → cheap same-core context switch, ~5 µs. Background load "helps" by accidentally forcing
co-location.
- F ruled out: cycles/ref-cycles = 1.347 both conditions ⇒ when-running frequency identical.
- I ruled out: cross-core BUSY ≈ cross-core IDLE (~11 µs) ⇒ idle/HLT is not the cause.
- Confound handled: Probe 4 (cross-core, no contention) ≈ Probe 5 (cross-core, spinner
  contention) ⇒ slowness is the cross-core placement, not CPU contention.
- Method note: the original pin-to-one-core probe changed idle AND placement together; the
  student flagged this; Phase 2B held placement fixed and flipped only idle, which is what
  reversed the verdict. (Don't kill a hypothesis on an uncontrolled experiment.)

### Phase 4 evidence (deck-sanctioned: histogram + sampled callgraph + source)
- **Off-CPU latency histogram** (bpftrace, sched_switch): no-load mode **[8,16) µs** (139k);
  with-load mode **[2,4) µs** (312k). Whole distribution shifts down ~4×, matching 11→5 µs.
  [p4_offcpu_hist_{noload,load}.txt]
- **Sampled callgraph** (perf record -e sched:sched_switch -g): no-load shows the two pipe
  ends on SEPARATE cores blocking to the idle task —
  `lat_pipe ... S ==> swapper/0` and `... ==> swapper/3`, via
  `anon_pipe_read → schedule → __schedule`. Direct picture of cross-core spread + idle.
  [p4_callgraph_noload.txt]
- Per-CPU switch distribution [p2b_latpipe_cpudist_*]: AGGREGATE over many iterations, muddy —
  treated as weak/supporting only, not headline evidence.

### Kernel-source confirmation — kernel/sched/fair.c (KVM guest kernel 7.0.0-15)
- Wakeup placement path: `try_to_wake_up → select_task_rq → select_task_rq_fair (fair.c:8579)
  → select_idle_sibling (fair.c:7836)`.
- In `select_idle_sibling` [p4_select_idle_sibling.txt], the early outs take `target`/`prev`
  only if they are already idle (lines 24–26, 31–37). The decisive step is **line 110**
  `i = select_idle_cpu(p, sd, has_idle_core, target)` — it scans the LLC sched-domain for ANY
  idle CPU and returns it. With idle CPUs available (no bg load) it finds one and places the
  woken pipe partner there → the two ends land on different cores → every hot-potato round-trip
  is a cross-core wakeup (reschedule IPI + pipe-buffer/task-struct cache-line bounce) ≈ 11 µs.
  Under bg load, `select_idle_cpu` finds no idle CPU (returns past line 111) → falls through to
  `prev`/`target` → the partner is co-located with the waker → same-core context switch ≈ 5 µs.
- (Student to confirm/own the line identification.)

### Kernel-source confirmation
- File / function (path under /usr/src):
- What the code does:
- How it explains the measurement:

### Conclusion (write last)

---

## Task 2 — Random-read gap from prepare block size (v1 vs v2)

### Environment
(same fields as above)

### Baseline + variance
- v1 reads/s (n=  ):
- v2 reads/s (n=  ):
- Gap vs run-to-run noise:

### Hypotheses (candidates — fill Result after measuring; see task2/RUNBOOK.md)
| # | Hypothesis | Prediction | Discriminating measurement | Result | Evidence file |
|---|-----------|-----------|----------------------------|--------|---------------|
| 1 | Disk fragmentation: v1 small writes → many extents → slower reads | v1 has many more extents; v1 run does disk I/O | `filefrag` extent counts + block I/O during run | | |
| 2 | Both files fully page-cached → run is RAM-bound, disk layout irrelevant | ~0 block I/O in both runs | `vmtouch` / `block:block_rq_issue` count | | |
| 3 | Page-cache folio order: 4M prepare builds larger folios → fewer faults / dTLB misses on read | v2 has fewer minor-faults and/or dTLB-load-misses; instructions ~equal | `perf stat minor-faults,dTLB-load-misses` v1 vs v2; folio-order histogram | | |

### Ruled-out alternatives (explicit)
- (record here WITH data — e.g. "fragmentation ruled out: v1=N extents but block I/O ≈ 0, so reads never hit disk")

### Kernel-source confirmation
- File / function:
- Mechanism:

### Conclusion (write last)
