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
- **Final connection (confirmed):** `select_task_rq_fair` (fair.c:8579) fast-path line 64 calls
  `select_idle_sibling`, whose **line 110** `select_idle_cpu` scans the LLC domain for an idle
  CPU. Idle system → returns an idle sibling → pair spread cross-core → ~11 µs. Loaded → no idle
  CPU found → returns prev/target → pair co-located same-core → ~5 µs.

### Conclusion — TASK 1 COMPLETE
Mechanism = scheduler wake-placement (`select_idle_sibling`/`select_idle_cpu`). F ruled out
(constant cycles/ref-cycles), I ruled out (cross-core-busy still slow), P confirmed by
placement-controlled toggle + histogram + callgraph + source. Full writeup: `writeup/task1.md`.

### Kernel-source confirmation
- File / function (path under /usr/src):
- What the code does:
- How it explains the measurement:

### Conclusion (write last)

---

## Task 2 — Random-read gap from prepare block size (v1 vs v2)

### Environment  [task2/p0_env.txt]
- Same KVM guest, kernel 7.0.0-15. **RAM 7.7 GiB** (assignment said 16; irrelevant — 64 MiB
  caches fine). $HOME on **ext4** (`/dev/vda1`, opts incl. `discard`, `commit=30`).
- **THP = [always]** ← prime suspect for the missing gap.
- sysbench 1.0.20.

### Baseline + variance  [task2/p1_baseline_gap.txt]  ⚠ GAP DID NOT REPRODUCE
- v1 reads/s (n=5): median **1,933,791** (1.904–1.936 M)
- v2 reads/s (n=5): median **1,931,441** (1.878–1.936 M)
- Gap: **~0% (v2 marginally slower).** Assignment expects v2 +8–9%. The effect is ABSENT here.
- Discipline: cannot investigate an unreproducible effect. Question flips to: WHY is the gap
  absent in this environment? Leading hypothesis: **THP=always gives large folios/huge pages to
  BOTH files regardless of prepare block size, erasing the write-size-dependent folio difference**
  that the gap depends on. Reference env was likely THP=madvise. → Phase 1B diagnoses this.

### Hypotheses — RESULTS (Phase 2, THP=always as shipped)
| # | Hypothesis | Discriminating measurement | Result |
|---|-----------|----------------------------|--------|
| 1 | Disk fragmentation | filefrag + major-faults | **RULED OUT** — v1=2 extents, v2=1 (trivial); page-faults are 100% MINOR (1813=1813, 821=821), zero major ⇒ fully cached, zero disk I/O ⇒ layout irrelevant. |
| 3 | Write block size → page-cache folio size → fewer faults | `perf stat page-faults` v1 vs v2 | **CONFIRMED (first link)** — v2 821 vs v1 1813 page-faults (2× fewer). minor-faults≈folios touched ⇒ v1≈9 pages/folio (~36 KB), v2≈20 pages/folio (~80 KB). 4M writes build larger folios. |
| 3b | …→ fewer dTLB misses → faster reads | `dTLB-load-misses`, `FilePmdMapped`, reads/s | **NOT here** — dTLB identical (8.74M vs 8.75M), FilePmdMapped=0 for BOTH, `do_set_pmd` never fired, reads/s within 0.8%. Larger folios are still sub-PMD (20 ≪ 512 pages) ⇒ 4 KB PTEs ⇒ no dTLB benefit ⇒ no throughput gap. |

### Why the gap is absent here (the key finding)
- The benchmark is **dTLB-bound**: 8.7M dTLB-misses / 9.4M reads ≈ **0.93 miss per read** (4 KB TLB
  reach ~4 MB ≪ 64 MB working set ⇒ constant thrash). Only HUGE pages (2 MB → 64 MB in ~32 TLB
  entries) remove this.
- v2's 4M writes DO build larger folios (proven: 2× fewer faults) but they stay **sub-PMD**, so
  they're 4 KB-mapped → dTLB unchanged → throughput unchanged.
- The reference 8–9% gap requires v2's folios to reach **PMD huge mapping** (FilePmdMapped>0),
  cutting dTLB misses. In this VM that never happens (do_set_pmd never fires). Same trap as the
  lecture's Redis case: THP=always ≠ huge pages actually used.
### Phase 3 results — mechanism confirmed; gap DECOMPOSED
- **Folio order (3a)** [p3_folios_*]: folios built during prepare v1=7265, v2=3228 ⇒ v1 ~2.3
  pages/folio, v2 ~5.1 ⇒ v2 builds **~2.2× larger folios.** Direct confirmation of write-size→folio-size.
- **THP sweep (3b-i)** [p3_thp_sweep]: v2 ~1% > v1 in always/madvise/never — a small, genuine,
  **THP-independent** edge; no jump to 8–9% in any mode.
- **1 GiB (3b-ii)** [p3_bigfile]: page-faults v1=17173 vs v2=1301 (**13× fewer** — folio effect
  scales with size), but dTLB-misses identical (7.41M both), reads/s ~1.5% apart. Folios still sub-PMD.
- **Decomposition:** the documented 8–9% gap = a small folio/fault term (~1%, which we DO reproduce)
  + a dTLB/huge-page term (the rest) that is **gated off here** because folios never reach PMD, so
  FilePmdMapped=0 and dTLB is unchanged. THP mode and file size do not break the sub-PMD ceiling.
- ROOT CAUSE: prepare block size sets page-cache folio order; the throughput payoff requires those
  folios to be PMD-huge-mapped (dTLB reduction). On this kernel mmap'd ext4 reads stay sub-PMD →
  only the ~1% fault term survives. → Phase 4: confirm the folio-order cap + PMD condition in /usr/src.

### Kernel-source confirmation  [p4_*]
- `mm/readahead.c:467 page_cache_ra_order`: `new_order = min(mapping_max_folio_order,
  ilog2(ra->size))` ⇒ folio order capped by I/O request size (v2 4M writes→larger; v1→smaller;
  random reads never grow ra->size).
- `mm/memory.c:5408 do_set_pmd`: installs a 2 MiB PMD only for PMD-order aligned folios.
- **Decisive:** `/boot/config-7.0.0-15-generic` → `# CONFIG_READ_ONLY_THP_FOR_FS is not set`
  (CONFIG_TRANSPARENT_HUGEPAGE=y). File-backed THP for regular FS NOT compiled in ⇒ mmap'd ext4
  reads can NEVER get a PMD mapping ⇒ FilePmdMapped=0 structurally ⇒ dTLB win unreachable ⇒
  8–9% gap impossible on this kernel.

### Phase 5 — folio histogram CORRECTS the earlier "sub-PMD" claim
- [p5_folio_hist_*] v1 = 4096× order-2 (16 KiB); **v2 = 32× order-9 (2 MiB / PMD) = whole file.**
  Earlier Phase-3 inference "folios stay sub-PMD" was WRONG (averaged counts); the histogram shows
  v2 DOES reach PMD-order folios. The gate is the PMD *mapping*, not the folio size.
- [p5_thp_dtlb_test] anon THP microbench INCONCLUSIVE — MADV_HUGEPAGE never engaged THP
  (AnonHugePages=0 both), so dTLB lever magnitude not measured. Not contrary evidence.

### Conclusion — TASK 2 COMPLETE
Root cause = prepare block size sets page-cache folio order: v2 4M writes build 2 MiB PMD-order
folios (proven by histogram), v1 stays 16 KiB. The 8–9% is the dTLB win from PMD-MAPPING v2's huge
folios; structurally disabled here (CONFIG_READ_ONLY_THP_FOR_FS unset → do_set_pmd never fires →
FilePmdMapped=0 → 4K PTEs → dTLB unchanged), so only the ~1% fault term reproduces. Fragmentation
ruled out (zero disk I/O). Writeup: task2.md.
- **dTLB magnitude MEASURED [p5c]:** same random-read pattern, 2 MiB hugetlb vs 4 KiB → dTLB-misses
  194.7M→2.7K (256M) / 198.8M→13.6K (1G), throughput +12.7% (256M) / +20.2% (1G). Brackets the 8–9%.
  PMD mapping IS worth the gap on this CPU; v2 just can't reach it (file-cache PMD gated off). Chain
  fully evidenced (no link assumed). Caveat: anon MADV_COLLAPSE/THP didn't engage on this kernel
  (EINVAL); hugetlb used as the reliable toggle.
