# Performance Engineering (2360013) — Assignment 1

**Student:** חביב ראמי · ID 325420180
**Instructor:** Nadav Amit · Spring 2026
**Environment:** provided VM `course-08` — KVM guest, Intel Xeon Gold 5420+, 4 vCPUs,
kernel 7.0.0-15-generic, ext4, 7.7 GiB RAM. Kernel sources at `/usr/src/linux-7.0.0`.

---

## How this investigation was run
Each phenomenon was driven as a hypothesis loop: establish a baseline **with run-to-run
variance**, predict before measuring, find the single measurement that separates competing
hypotheses, **rule out alternatives with data**, and **confirm the mechanism against the kernel
source under `/usr/src`**. Raw tool output for every probe is filed under `task1/` and `task2/`
(referenced inline by filename); the full timestamped trail is in the git history.

## Results at a glance
- **Task 1 — pipe latency improves under CPU load.** Cause: **scheduler wake-placement**. With
  idle CPUs available, `select_idle_sibling()` spreads the two pipe partners onto *different*
  cores, making every round-trip a cross-core wakeup (~11 µs); background load removes the idle
  CPUs, so the pair is co-located on one core (~5 µs). Frequency (DVFS) and idle/HLT cost were
  ruled out with data; confirmed at `kernel/sched/fair.c:select_idle_sibling`.
- **Task 2 — v2 (4 MiB prepare) reads faster.** Cause: **prepare block size sets page-cache folio
  order** — v2 builds 2 MiB PMD-order folios, v1 stays at 16 KiB; the 8–9 % is the **dTLB win from
  PMD-mapping** those folios (the lever measured here at +12.7–20.2 % via hugetlb). **On this VM image
  the gap does not reproduce** — v1 and v2 are statistically indistinguishable, because the ext4
  file-THP path is compiled out (`CONFIG_READ_ONLY_THP_FOR_FS` unset). The assignment expects the gap
  on this VM, so this contradiction was raised with the instructor (Task 2 §8). Fragmentation ruled
  out (zero disk I/O); confirmed in `mm/readahead.c`, `mm/memory.c`, and the kernel config.

---
