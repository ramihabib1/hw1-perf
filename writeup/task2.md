# Task 2 — Root cause of the v1/v2 random-read gap (prepare block size)

**Setup.** Same 64 MiB file built two ways — v1 with default (~16 KiB) prepare writes, v2 with
4 MiB writes — then the identical random-read (`mmap`, 4 KiB, 5 s) benchmark on each. Reference:
v2 ~8–9 % faster.

**Result (one line).** The prepare block size sets the **page-cache folio size** (v2's 4 MiB
writes build ~2.2× larger folios). That gives v2 *measurably* fewer page-faults and a small
(~1 %) edge. The full 8–9 % is a **dTLB** effect that only appears when those folios are
**PMD (2 MiB) huge-mapped** — and on this kernel that is **structurally disabled**
(`CONFIG_READ_ONLY_THP_FOR_FS` is not set), so mmap'd ext4 reads can never get a huge mapping
(`FilePmdMapped=0`). The gap is therefore decomposed: the folio/fault term reproduces; the
dTLB term is gated off here.

---

## 1. Environment  [task2/p0_env.txt]
KVM guest, kernel 7.0.0-15, **$HOME on ext4** (`/dev/vda1`, opts incl. `discard`). RAM 7.7 GiB
(irrelevant — 64 MiB caches fully). Runtime THP = `[always]`; sysbench 1.0.20.

## 2. Baseline — the gap did not reproduce  [task2/p1_baseline_gap.txt]
| | v1 reads/s | v2 reads/s |
|---|---|---|
| median (n=5) | 1,933,791 | 1,931,441 |

v2 is *not* ~8–9 % faster here (within noise). So the investigation became: **why is the gap
absent, and what is its real mechanism?** (You cannot investigate an effect you cannot reproduce.)

## 3. Rule out the obvious wrong answer — disk fragmentation  [p1b_filefrag, p2_perfstat_*]
- `filefrag`: v1 = **2 extents**, v2 = **1** — trivial, not the "many extents" fragmentation story.
- `perf stat`: **`page-faults` == `minor-faults` exactly** (v1 1813=1813, v2 821=821) → **zero major
  faults** → the file is 100 % page-cached, **no disk I/O during the run**.
- Disk layout cannot affect a run that never touches disk. **Fragmentation ruled out, with data.**

## 4. The mechanism — prepare block size → page-cache folio size  [p2_perfstat_*, p3_folios_*]
Following the lecture's method (caching deck s.58/64: page-faults headline + count huge-page usage):

| | page-faults (64 MiB) | folios built (prepare) | pages/folio |
|---|---|---|---|
| v1 | 1813 | 7265 | ~2.3 |
| v2 | **821** (2× fewer) | 3228 | **~5.1** (2.2× larger) |

v2's 4 MiB writes build **~2.2× larger folios**; fewer, larger folios ⇒ fewer minor faults on
the mmap read path (fault-around installs more PTEs per fault). The effect **scales with file
size**: at **1 GiB**, page-faults were v1 **17173** vs v2 **1301** (~13× fewer) [p3_bigfile.txt].
The write-size → folio-size link is confirmed and reproducible.

## 5. Why the *throughput* gap is absent — it's a dTLB effect, and it's gated  [p2_*, p3_*]
- The benchmark is **dTLB-bound**: `dTLB-load-misses` ≈ **8.74 M over 9.4 M reads ≈ 0.93 miss/read**
  (4 KiB TLB reach ~4 MiB ≪ 64 MiB working set). Only **huge pages** remove this (2 MiB → 64 MiB
  in ~32 TLB entries).
- But v2's larger folios are **sub-PMD** (~5 pages ≪ 512), so they are mapped with 4 KiB PTEs →
  `dTLB-load-misses` **identical** v1 vs v2 (8.74 M vs 8.75 M; 7.41 M vs 7.41 M at 1 GiB) →
  identical cycles → only the **~1 %** fault-overhead edge survives.
- This holds across **all THP modes** (always/madvise/never: v2 ~1 % > v1, no jump) and at
  **1 GiB** — the sub-PMD ceiling is never broken. [p3_thp_sweep.txt, p3_bigfile.txt]
- Direct check: `FilePmdMapped = 0 kB` for both, and `do_set_pmd` **never fired** (bpftrace).
  No huge mapping happens for either file. [p2_smaps_*, p2_faultcount_*]

**Decomposition of the documented 8–9 %:** a small folio/fault term (~1 %, reproduced here) +
a dTLB/huge-page term (the rest) that requires PMD mapping — which this kernel does not provide.

## 6. Kernel-source confirmation  [p4_page_cache_ra_order, p4_do_set_pmd, p4_kconfig_thp]
- **Folio order is bounded by I/O size** — `mm/readahead.c:467 page_cache_ra_order()`:
  ```c
  new_order = min(mapping_max_folio_order(mapping), new_order);
  new_order = min_t(unsigned int, new_order, ilog2(ra->size));   /* ← capped by request size */
  ```
  Larger writes (v2) permit a larger order; small writes (v1) force a small one; both are capped,
  and random reads never grow `ra->size`. Explains §4.
- **Huge file mapping needs a PMD-order folio AND the feature compiled in** — `mm/memory.c:5408
  do_set_pmd()` installs a 2 MiB PMD only for a PMD-sized aligned folio.
- **The decisive gate** — `/boot/config-7.0.0-15-generic`:
  ```
  CONFIG_TRANSPARENT_HUGEPAGE=y
  # CONFIG_READ_ONLY_THP_FOR_FS is not set      ← file-backed THP for regular FS NOT compiled in
  ```
  With `READ_ONLY_THP_FOR_FS` off, an mmap'd **ext4** read can **never** receive a PMD file
  mapping. So `FilePmdMapped=0` is **structural**, the dTLB win is unreachable, and the 8–9 %
  gap is **impossible on this kernel** — regardless of folio size, THP mode, or file size.

## 7. Conclusion
The gap's root cause is **page-cache folio size, set by the prepare write block size** (v2's
4 MiB writes → larger folios → fewer page-faults; confirmed, and scaling 2×→13× with file size).
Its *throughput* magnitude, however, comes from **PMD huge-page mapping** of those folios cutting
the dTLB misses that dominate this benchmark. On this VM that mapping is structurally disabled
(`CONFIG_READ_ONLY_THP_FOR_FS` unset, `do_set_pmd` never reached), so only the ~1 % folio/fault
term reproduces and the 8–9 % stays dormant. Fragmentation was ruled out (zero disk I/O).
The reference environment must build the kernel with file-backed THP enabled (or otherwise reach
PMD-mapped file folios), which is where the dTLB-driven 8–9 % comes from.

## Evidence index
| File | Evidence |
|---|---|
| `task2/p0_env.txt` | environment (ext4, THP=always, sysbench) |
| `task2/p1_baseline_gap.txt` | gap absent (v1≈v2) |
| `task2/p1b_filefrag.txt` | fragmentation trivial (2 vs 1 extent) |
| `task2/p2_perfstat_{v1,v2}.txt` | page-faults 2× fewer (v2); dTLB identical; 100 % minor |
| `task2/p2_smaps_{v1,v2}.txt`, `p2_faultcount_*` | FilePmdMapped=0; do_set_pmd never fires |
| `task2/p3_folios_{v1,v2}.txt` | folios built: v1 7265 vs v2 3228 (~2.2× larger) |
| `task2/p3_thp_sweep.txt` | v2 ~1 % > v1 in all THP modes; no jump |
| `task2/p3_bigfile.txt` | 1 GiB: 13× fewer faults, dTLB still equal, ~1.5 % |
| `task2/p4_page_cache_ra_order.txt` | folio order capped by I/O size |
| `task2/p4_do_set_pmd.txt` | PMD file-map path |
| `task2/p4_kconfig_thp.txt` | `CONFIG_READ_ONLY_THP_FOR_FS` not set — the structural gate |
