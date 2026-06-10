# Investigation Plan — how we actually solve HW1

The grade rewards the **trail**, not the answer: hypotheses, the single measurement that
killed each wrong one, explicit ruled-out alternatives, and confirmation in `/usr/src`.
So we run this as an **adaptive funnel**, not a fixed script — each measurement decides
the next probe.

## Operating model (the loop)
```
VM-Claude executes a batch  ──push──▶  Mac-Claude analyzes + student predicts
        ▲                                          │
        └──────────  Mac writes the NEXT batch  ◀──┘  (branch on what the data showed)
```
- We do **not** front-load all commands. Phase 2 depends on Phase 1's numbers.
- Before each measured run, **the student writes a prediction** in notes.md. Predict-then-measure.
- Every probe → raw file + timestamped commit. The commit log is the submission trail.

## Core reframe (both tasks)
Both puzzles dissolve once you ask *"what is actually being held constant, and what is the
one thing that differs?"* — then find the single counter that exposes the difference.

---

## Task 1 — pipe latency DROPS ~2× under CPU load
`lat_pipe` does a fixed amount of work per round-trip. So:
```
latency ≈ (work_cycles / frequency)  +  wakeup_overhead_per_roundtrip
```
Background load can only help via those two terms. Three candidate mechanisms:

| # | Mechanism | Why load would help | Discriminator (single measurement) | One-knob falsification (NO load) |
|---|-----------|--------------------|-------------------------------------|----------------------------------|
| F | **Frequency / DVFS** | bursty pipe never raises util → governor keeps freq low; hogs pin freq high | `perf stat`: instructions ~equal, **cycles/time differ**; turbostat **Bzy_MHz** | `cpupower -g performance` → latency collapse? |
| I | **Idle-exit latency** (C-states; guest: HLT→VMEXIT) | idle CPU sleeps deep; partner wakeup pays exit cost; hogs keep CPU in C0 | turbostat **C-state residency**; wakeup latency | `cpupower idle-set -D 0` → latency collapse? |
| P | **Scheduler placement** | unloaded pair bounces across idle CPUs (cold, cross-CPU IPI); load co-locates | `perf stat` **cpu-migrations**; `sched:*` trace | `taskset -c 0` pin → reproduces low latency? |

**The key experimental design — decompose the 2×, don't pick a winner.**
F and I are entangled (a busy CPU is both high-freq *and* never idle). Isolate them with a
2×2, all with **no background load**:

```
                  idle states ENABLED        idle states DISABLED
  gov as-shipped     (baseline, slow)         Δ = idle-exit component
  gov performance    Δ = frequency component  (should ≈ loaded latency)
```
The drop from baseline → (perf gov) measures the **frequency** share; the further drop when
idle is disabled measures the **idle-exit** share. That decomposition *is* the answer.

**Environment fork to resolve first:** is this a KVM guest? Check
`/sys/devices/system/cpu/cpuidle/current_driver`. If `haltpoll`/`none`, the "C-state" story
is really vCPU halt/poll (`halt_poll_ns`), and turbostat may not show host C-states — the
mechanism and the source file change accordingly.

**Source confirmation (pick per winner):**
- F → `drivers/cpufreq/intel_pstate.c`, `kernel/sched/cpufreq_schedutil.c` (util→freq map)
- I → `drivers/cpuidle/governors/*` (idle-duration prediction), driver `exit_latency`; guest → `cpuidle-haltpoll`
- P → `kernel/sched/fair.c` (`select_task_rq_fair`, wake-affine)

**Student's open question (predict before Phase 2):** which of F/I/P dominates, and which
single number proves it? Put the bet on the board first.

---

## Task 2 — v2 (4 MiB prepare) reads ~8–9% faster
Identical run, identical 64 MiB of data, **both fully page-cached** → the run never touches
disk. So the only thing that can differ is **how the page cache was populated at prepare
time, and what persists into the read phase.** A 4-step funnel:

**Step 1 — Rule out the seductive wrong answer (disk fragmentation).**
v1's small writes fragment the file; v2's 4 MiB writes don't. Show it's irrelevant *with data*:
- `filefrag` → v1 has many more extents (the tempting story), AND
- block I/O ≈ 0 during the run / both files fully resident (`vmtouch`) → reads are RAM-bound.
→ Fragmentation **ruled out**: layout can't matter if disk is never read.

**Step 2 — Localize the cost (counter-driven, don't assume).**
`perf stat` v1 vs v2 on `{instructions, cycles, minor-faults, dTLB-load-misses, LLC-load-misses}`.
Instructions ~equal (same #reads). **Whichever counter differs is the smoking gun** — we
follow *that*, not a pre-chosen story.
- Working hypothesis: 4 MiB writes build **large folios** in the page cache; 16 KiB writes
  build order-0 (4 KiB) folios. Large folios → fewer page-table entries / possible huge
  mapping → **fewer dTLB misses**, and fault-around installs more PTEs per fault → **fewer
  minor-faults**. *Prediction to test, not a conclusion.* If those counters don't move, the
  hypothesis is wrong and we chase the counter that did.

**Step 3 — Observe the mechanism directly.**
Confirm the folio-size difference exists: `/proc/<pid>/smaps` (FilePmdMapped / large mappings)
during a run, or trace folio allocation order during each prepare. v2 should show larger folios.

**Step 4 — Confirm in `/usr/src`.**
- `mm/readahead.c` → `page_cache_ra_order` (how folio order is chosen)
- `mm/filemap.c` → `filemap_fault`, `filemap_map_pages` (fault-around over a large folio)
- the path from large folios → fewer TLB entries / faults

**Student's open question:** before Step 2, predict which counter differs and by roughly how
much. The counter you pick is your hypothesis made falsifiable.

---

## Definition of done (per task = one writeup section)
1. Environment as-shipped + baseline **with run-to-run variance** (median/min/spread).
2. Hypothesis table: each candidate **ruled in/out by a named measurement** (evidence file).
3. The mechanism — for Task 1, the F/I decomposition; for Task 2, the localized counter.
4. Kernel-source citation: `path:function`, what it does, how it produces the numbers.
5. Raw logs referenced by filename. The git history shows the order you discovered it in.
