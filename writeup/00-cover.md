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
- **Task 2 — v2 (4 MiB prepare) reads ~9 % faster (reproduced).** Cause: **prepare block size sets
  page-cache folio order** — v2's 4 MiB writes build 2 MiB PMD-order folios, v1 stays at 16 KiB.
  v2's folios are **mapped as 2 MiB huge pages** (`FilePmdMapped = 64 MiB`, v1 = 0), which **collapses
  dTLB misses ~475× (8.79 M → 18 K)**; since the random read is dTLB-bound, that is the +9–10 %. It is
  a TLB effect (LLC misses equal), not disk (zero I/O). Confirmed in `mm/memory.c:do_set_pmd` and
  `mm/readahead.c:page_cache_ra_order`. *(The effect was initially hidden by memory fragmentation
  after 22 days of VM uptime — a reboot restored it; see Task 2 §7.)*

---
