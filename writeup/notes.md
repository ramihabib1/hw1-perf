# HW1 — Investigation Notes

Running hypothesis log. The final writeup is assembled from this. Keep it honest:
record the hypotheses that were WRONG and how you killed them — that's most of the grade.

---

## Task 1 — Pipe latency improves under background CPU load

### Environment (as shipped, before any changes)
- CPU model / cores / threads:
- Governor / driver / turbo state:
- perf_event_paranoid:
- Kernel version (`uname -r`):

### Baseline + variance
- Command:
- Runs (n=  ), reported as (mean/median/min, and why):
- No-load result:
- With-load result:
- Effect size vs noise:

### Hypotheses (candidates — fill Result after measuring; see task1/RUNBOOK.md)
| # | Hypothesis | Prediction (counter/behavior) | Discriminating measurement | Result: ruled in / out | Evidence file |
|---|-----------|-------------------------------|----------------------------|------------------------|---------------|
| 1 | DVFS: governor keeps freq low under bursty pipe load; bg load pins freq high | cycles/run higher unloaded; instructions ~equal; Bzy_MHz lower unloaded | `perf stat cycles,instructions` + `turbostat`; knob: `-g performance` no-load | | |
| 2 | Idle/halt-exit latency: idle CPU enters deep C-state (guest: HLT VM-exit); partner wakeup pays exit cost | C-state residency high unloaded; latency falls when deep idle disabled | `turbostat` C-state cols; knob: `cpupower idle-set -D 0` no-load | | |
| 3 | Scheduler placement: unloaded pair bounces across idle CPUs (cold, cross-CPU IPI); load co-locates | more cpu-migrations unloaded; pinning reproduces low latency | `perf stat cpu-migrations`; `trace-cmd sched_*`; knob: `taskset -c 0` | | |

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
