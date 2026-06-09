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

### Hypotheses
| # | Hypothesis | Prediction (counter/behavior) | Discriminating measurement | Result: ruled in / out | Evidence file |
|---|-----------|-------------------------------|----------------------------|------------------------|---------------|
| 1 |           |                               |                            |                        |               |
| 2 |           |                               |                            |                        |               |

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

### Hypotheses
| # | Hypothesis | Prediction | Discriminating measurement | Result | Evidence file |
|---|-----------|-----------|----------------------------|--------|---------------|
| 1 |           |           |                            |        |               |
| 2 |           |           |                            |        |               |

### Ruled-out alternatives (explicit)
-

### Kernel-source confirmation
- File / function:
- Mechanism:

### Conclusion (write last)
