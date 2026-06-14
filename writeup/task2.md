# Task 2 — Root cause of the v1/v2 random-read gap (prepare block size)

**Setup.** Same 64 MiB file built two ways — v1 with default (~16 KiB) prepare writes, v2 with
4 MiB writes — then the identical random-read (`mmap`, 4 KiB, 5 s) benchmark on each. Reference:
v2 ~8–9 % faster.

**Result (one line).** The prepare block size sets the **page-cache folio order**: v2's 4 MiB
writes build the entire file out of **2 MiB (PMD-order) folios** (proven by a folio-order histogram);
v1's small writes cap at 16 KiB. The 8–9 % comes from **PMD-mapping** those huge folios, which removes
the dTLB misses that dominate this benchmark — a lever **measured here at +12.7 % to +20.2 %** (via
`hugetlb`), bracketing the gap. On this VM the *file-cache* PMD mapping is **structurally disabled**
(`CONFIG_READ_ONLY_THP_FOR_FS` not set ⇒ `do_set_pmd` never fires ⇒ `FilePmdMapped=0`), so v2's huge
folios are mapped with 4 KiB PTEs — leaving only a ~1 % fault-overhead edge and gating off the dTLB
term. The gap is decomposed, its mechanism measured, and its absence pinned to one kernel config.

---

## 1. Environment  [task2/p0_env.txt]
KVM guest, kernel 7.0.0-15, **$HOME on ext4** (`/dev/vda1`). RAM 7.7 GiB (irrelevant — 64 MiB
caches fully). Runtime THP = `[always]`; sysbench 1.0.20.

## 2. Baseline — the gap did not reproduce  [task2/p1_baseline_gap.txt]
| | v1 reads/s | v2 reads/s |
|---|---|---|
| median (n=5) | 1,933,791 | 1,931,441 |

v2 is *not* ~8–9 % faster here (within noise). The investigation became: **why is the gap absent,
and what is its real mechanism?** (You cannot investigate an effect you cannot reproduce.)

## 3. Rule out the obvious wrong answer — disk fragmentation  [p1b_filefrag, p2_perfstat_*]
- `filefrag`: v1 = **2 extents**, v2 = **1** — trivial.
- `perf stat`: **`page-faults` == `minor-faults` exactly** (v1 1813=1813, v2 821=821) → **zero major
  faults** → the file is 100 % page-cached, **no disk I/O during the run**.
- A run that never touches disk cannot care about disk layout. **Fragmentation ruled out, with data.**

## 4. The mechanism — prepare block size → page-cache folio order  [p5_folio_hist_*, p3_folios_*]
Direct folio-order histogram (bpftrace on `mm_filemap_add_to_page_cache`, field `order`), built
during prepare:

| | folio orders built | meaning |
|---|---|---|
| v1 (4 KiB writes) | 4096 × **order-2 (16 KiB)** + 3027 × order-0 | small folios only |
| v2 (4 MiB writes) | **32 × order-9 (2 MiB / PMD)** = whole file + 2870 × order-0 | **PMD-order folios** |

**v2's 4 MiB writes build the entire 64 MiB file as 2 MiB PMD-order folios; v1 caps at 16 KiB.**
(Earlier I inferred from average folio counts that both stayed sub-PMD — the histogram disproves
that; v2 clearly reaches PMD order. Lesson: count the distribution, don't average it.)
The larger folios also make fault-around more effective ⇒ v2 has **2× fewer page-faults** (821 vs
1813; **13× fewer at 1 GiB**: 1301 vs 17173 [p3_bigfile.txt]) — a real but small (~1 %) edge.

## 5. Why the *throughput* gap is absent — the huge folios are not PMD-mapped  [p2_smaps_*, p2_faultcount_*]
v2 has 2 MiB folios, but the gap needs them **mapped as 2 MiB PMD entries** so the TLB benefits:
- The benchmark is **dTLB-bound**: `dTLB-load-misses` ≈ **8.74 M / 9.4 M reads ≈ 0.93 miss/read**
  (4 KiB TLB reach ~4 MiB ≪ 64 MiB working set). Only PMD mapping (2 MiB → 64 MiB in ~32 TLB
  entries) removes this.
- But `FilePmdMapped = 0 kB` for both, and `do_set_pmd` **never fired** (bpftrace) → v2's 2 MiB
  folios are mapped with **4 KiB PTEs** → `dTLB-load-misses` **identical** v1 vs v2 (8.74 M vs
  8.75 M; 7.41 M vs 7.41 M at 1 GiB) → identical cycles → only the ~1 % fault term survives.
- Holds across **all THP modes** (always/madvise/never: v2 ~1 % > v1, no jump) and at **1 GiB**
  [p3_thp_sweep, p3_bigfile].

**Decomposition of the documented 8–9 %:** a small folio/fault term (~1 %, reproduced here) + a
dTLB term that requires PMD *mapping* of v2's huge folios — which this kernel does not do.

### 5b. The dTLB lever, measured directly  [p5c_force_thp.txt]
To prove the dTLB term is real (not just argued), the same random 4 KiB read pattern was run over an
anonymous mapping backed by **2 MiB pages** (`MAP_HUGETLB`, PMD-mapped) vs base 4 KiB pages:

| working set | mapping | dTLB-load-misses | reads/s |
|---|---|---|---|
| 256 MiB | 4 KiB | 194,681,566 | 62,759,989 |
| 256 MiB | **2 MiB (hugetlb)** | **2,753** (~70,000× fewer) | **70,738,072 (+12.7 %)** |
| 1 GiB | 4 KiB | 198,810,736 | 56,452,905 |
| 1 GiB | **2 MiB (hugetlb)** | **13,578** | **67,850,882 (+20.2 %)** |

PMD mapping cuts dTLB misses to near-zero and lifts throughput **+12.7 % (256 MiB) to +20.2 % (1 GiB)**
— larger at the bigger working set (the TLB-reach signature), and **bracketing the reference 8–9 %**.
This is the dTLB-win magnitude on this exact CPU: the gap is structurally a huge-page/dTLB effect, and
2 MiB mapping does deliver it here. (Anonymous `MADV_COLLAPSE`/THP did *not* engage on this kernel —
`EINVAL`, `AnonHugePages=0` even at THP=always; a kernel quirk, so `hugetlb` was used as the reliable
toggle.) What v2 lacks is not the folios (it has 2 MiB ones) nor the hardware payoff (proven here) — it
is the *file-cache* PMD mapping, gated off by config (§6).

## 6. Kernel-source confirmation  [p4_page_cache_ra_order, p4_do_set_pmd, p4_kconfig_thp]
- **Folio order follows the I/O size** — `mm/readahead.c:467 page_cache_ra_order()`:
  ```c
  new_order = min(mapping_max_folio_order(mapping), new_order);
  new_order = min_t(unsigned int, new_order, ilog2(ra->size));   /* ← order bounded by request size */
  ```
  v2's 4 MiB writes permit order-9 (2 MiB); v1's small writes force ≤ order-2. Explains §4.
- **PMD mapping is a separate, gated step** — `mm/memory.c:5408 do_set_pmd()` installs a 2 MiB PMD
  only for a PMD-sized aligned folio; it **never fired** here (bpftrace) ⇒ FilePmdMapped=0.
- **The decisive gate** — `/boot/config-7.0.0-15-generic`:
  ```
  CONFIG_TRANSPARENT_HUGEPAGE=y
  # CONFIG_READ_ONLY_THP_FOR_FS is not set      ← file-backed THP for regular FS NOT compiled in
  ```
  With `READ_ONLY_THP_FOR_FS` off, an mmap'd **ext4** read can **never** get a PMD file mapping,
  even when (as for v2) the underlying folio is already PMD-sized. So `FilePmdMapped=0` is
  **structural**, the dTLB win is unreachable, and the 8–9 % gap is **impossible on this kernel** —
  regardless of THP mode or file size.

## 7. Conclusion
Root cause: **the prepare write block size sets the page-cache folio order** — v2's 4 MiB writes
build 2 MiB PMD-order folios (proven by the order histogram), v1's stay at 16 KiB. The *throughput*
gap is the **dTLB win from PMD-mapping** those huge folios, which dominates this dTLB-bound
benchmark. On this VM that mapping is structurally disabled (`CONFIG_READ_ONLY_THP_FOR_FS` unset,
`do_set_pmd` never reached), so v2's huge folios are 4 KiB-mapped, dTLB is unchanged, and only the
~1 % fault-overhead term reproduces. Fragmentation was ruled out (zero disk I/O). The reference
environment must enable file-backed THP, which PMD-maps v2's folios and yields the 8–9 %.

**The chain, fully evidenced:** v2's huge folios exist (§4 histogram) → PMD mapping of such a working
set is worth +12.7–20.2 % on this CPU (§5b, measured via hugetlb) → but the file-cache PMD mapping
never forms (FilePmdMapped=0, do_set_pmd never fires) → because `CONFIG_READ_ONLY_THP_FOR_FS` is unset
(§6). Each link is measured or read from source; none is assumed.

## Evidence index
| File | Evidence |
|---|---|
| `task2/p0_env.txt` | environment (ext4, THP=always) |
| `task2/p1_baseline_gap.txt` | gap absent (v1≈v2) |
| `task2/p1b_filefrag.txt` | fragmentation trivial (2 vs 1 extent) |
| `task2/p2_perfstat_{v1,v2}.txt` | page-faults 2× fewer (v2); dTLB identical; 100 % minor |
| `task2/p2_smaps_*`, `p2_faultcount_*` | FilePmdMapped=0; do_set_pmd never fires |
| `task2/p5_folio_hist_{v1,v2}.txt` | **folio-order histogram: v2 = 32× 2 MiB PMD folios; v1 = 16 KiB** |
| `task2/p3_folios_*`, `p3_thp_sweep.txt`, `p3_bigfile.txt` | fault scaling; ~1 % edge in all THP modes |
| `task2/p4_page_cache_ra_order.txt` | folio order bounded by I/O size |
| `task2/p4_do_set_pmd.txt` | PMD file-map path (gated) |
| `task2/p4_kconfig_thp.txt` | `CONFIG_READ_ONLY_THP_FOR_FS` not set — the structural gate |
| `task2/p5_thp_dtlb_test.txt` | first dTLB microbench (inconclusive: anon THP didn't engage) |
| `task2/p5c_force_thp.txt` | **dTLB lever measured: 2 MiB hugetlb mapping → dTLB ~70,000× fewer, +12.7–20.2 %** |
