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
| I | HLT→VMEXIT: idle reader HLTs → VMEXIT; wakeup pays VMENTER (+ host reschedule). Load = vCPUs never HLT | keeping the pipe core busy (no bg hogs) reproduces the speedup | **pin both ends to one core** `taskset -c 0` (core never idles) | **RULED IN (pending /usr/src)** — pin-to-one-core, NO load = 5.42 µs ≈ loaded 5.26 µs (vs unpinned no-load 11.1 µs). Idle removal alone reproduces the effect. | p2_pin1core_noload.txt |
| P | Scheduler placement: unloaded pair bounces across idle vCPUs (cold, cross-vCPU IPI); load co-locates | more cpu-migrations unloaded; pinning both ends reproduces low latency | `perf stat cpu-migrations`; `sched:*` trace | **NOT YET RULED OUT** — pin-to-one-core changed TWO variables (idle AND placement); and the "loaded case is cross-core" rescue was an UNMEASURED assumption. Need a placement-controlled experiment (Phase 2B). | (pending) |

**Verdict (PROVISIONAL — P still open):** F is ruled out by the constant cycles/ref-cycles
ratio (1.347 both conditions ⇒ when-running frequency identical). I is strongly indicated (pin
reproduces the speedup) but is **entangled with P** until we isolate idle from placement.
Phase 2B isolates them by holding placement = cross-core and flipping only idle. Caveat:
perf-stat latency (~69 µs) and totals are perf-overhead/calibration artifacts, NOT compared
across runs — only the frequency RATIO and the pin result are used.

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
