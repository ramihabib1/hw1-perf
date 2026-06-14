# Task 2 — Root cause of the v1/v2 random-read gap (prepare block size)

**Setup.** Same 64 MiB file built two ways — v1 with default (~16 KiB) prepare writes, v2 with
4 MiB writes — then the identical random-read (`mmap`, 4 KiB, 5 s) benchmark on each.

**Result (one line).** The prepare block size sets the **page-cache folio order**: v2's 4 MiB
writes build the whole file out of **2 MiB (PMD-order) folios**, v1's small writes cap at 16 KiB.
v2's huge folios get **mapped as 2 MiB huge pages** (`FilePmdMapped = 64 MiB`, the entire file;
v1 = 0), which **collapses dTLB misses ~475× (8.79 M → 18 K)** — and since this benchmark is
dTLB-bound, that is the **+9–10 %** speedup. Ruled out: disk fragmentation (zero disk I/O).
Confirmed against `mm/memory.c:do_set_pmd` and `mm/readahead.c:page_cache_ra_order`.

> **Investigative note.** The gap initially did **not** reproduce on the lab VM. We traced that to
> **memory fragmentation after 22 days of uptime** preventing the huge-page allocation (`do_set_pmd`
> never fired, `FilePmdMapped = 0`, anonymous THP also refused to engage). A reboot defragmented
> memory and the gap appeared immediately — see §7. The mechanism below is from the reproduced run.

---

## 1. Environment  [task2/p0_env.txt]
KVM guest, kernel 7.0.0-15, `$HOME` on **ext4**, THP = `[always]`, sysbench 1.0.20.

## 2. The gap — reproduced  [task2/p10_reboot_confirmed.txt]
| | v1 reads/s | v2 reads/s | v2 vs v1 |
|---|---|---|---|
| fresh-boot run | 1,942,670 | 2,124,442 | **+9.4 %** |

Matches the assignment's ~8–9 %. (On the long-uptime VM it measured flat — §7.)

## 3. Rule out the obvious wrong answer — disk fragmentation  [p1b_filefrag, p2_perfstat_*]
- `filefrag`: v1 = 2 extents, v2 = 1 — trivial.
- `perf stat`: **`page-faults` == `minor-faults` exactly** (zero major faults) → the file is 100 %
  page-cached, **no disk I/O during the run**. A run that never touches disk cannot care about disk
  layout. **Fragmentation ruled out, with data.**

## 4. The mechanism — confirmed end to end
**Step 1: prepare block size → folio order**  [p5_folio_hist_*]. Folio-order histogram (bpftrace on
`mm_filemap_add_to_page_cache`, captured during the buffered-**write** prepare):

| | folios built | |
|---|---|---|
| v1 (4 KiB writes) | 4096 × order-2 (16 KiB) | small folios only |
| v2 (4 MiB writes) | **32 × order-9 (2 MiB / PMD)** = whole file | PMD-order folios |

**Step 2: huge folios get PMD (huge-page) mapped**  [p10_reboot_confirmed.txt]. `smaps_rollup`
during a run:

| | FilePmdMapped |
|---|---|
| v1 | **0 kB** (16 KiB folios are 4 KiB-PTE mapped) |
| v2 | **65,536 kB = the entire 64 MiB file** (2 MiB folios mapped as PMD huge pages) |

**Step 3: huge mapping collapses dTLB misses → the speedup**  [p10_reboot_confirmed.txt]. `perf stat`,
same random-read pattern:

| | reads/s | **dTLB-load-misses** | cache-misses (LLC) |
|---|---|---|---|
| v1 | 1,886,428 | **8,790,240** (≈ 0.93 / read) | 130.6 M |
| v2 | 2,082,856 | **18,488** (≈ 0 / read; **~475× fewer**) | 126.8 M (**≈ equal**) |

The benchmark is **dTLB-bound** — a 64 MiB working set with 4 KiB pages thrashes the TLB (4 KiB
reach ≈ 4 MiB ≪ 64 MiB), so v1 misses on nearly every random read. v2's 2 MiB pages cover the file
in ~32 TLB entries → essentially **zero** dTLB misses → no page-walk stalls → more reads in the same
cycles → **+9–10 %**. The `cache-misses` (LLC) counter is **equal** between v1 and v2, which proves
the win is the **TLB**, not the cache.

### 4b. Independent magnitude check  [p5c_force_thp.txt]
Running the same random-read pattern over an explicit 2 MiB mapping (`MAP_HUGETLB`) vs 4 KiB pages
gave dTLB-misses ~194 M → ~3 K and **+12.7 % (256 MiB) / +20.2 % (1 GiB)** — confirming, independently
of the file path, that 2 MiB mapping of a TLB-bound random read is worth this order of magnitude.

## 5. Kernel-source confirmation  [p4_page_cache_ra_order, p4_do_set_pmd]
- **Folio order follows the I/O size** — `mm/readahead.c:467 page_cache_ra_order()`:
  `new_order = min(mapping_max_folio_order(mapping), ilog2(ra->size))` — the folio order is bounded
  by the request size. The buffered-write path that built our folios applies the same rule, so v2's
  4 MiB writes reach order-9 (2 MiB) and v1's small writes stay ≤ order-2. (§4 Step 1.)
- **The huge mapping** — `mm/memory.c:5408 do_set_pmd()` installs a 2 MiB PMD when the folio is
  PMD-order and the mapping is PMD-aligned. For v2 it fires (→ `FilePmdMapped = 64 MiB`); for v1 the
  16 KiB folio fails the `folio_order == HPAGE_PMD_ORDER` check and is PTE-mapped. (§4 Step 2.)

## 6. Conclusion
**Root cause:** the prepare write block size sets the page-cache folio order — v2's 4 MiB writes
build 2 MiB PMD-order folios, v1's stay at 16 KiB. v2's folios are then **mapped as 2 MiB huge pages**
(`FilePmdMapped = 64 MiB`), which **eliminates the dTLB misses** that dominate this random-read
benchmark (8.79 M → 18 K), yielding the **+9–10 %** gap. It is a **TLB** effect, not a cache effect
(LLC misses are equal) and not a disk effect (zero disk I/O). Every link is measured: folio order
(histogram) → PMD mapping (`FilePmdMapped`) → dTLB collapse (`perf stat`) → throughput → confirmed in
`do_set_pmd` / `page_cache_ra_order`.

## 7. Why it first didn't reproduce — memory fragmentation (the trail)
On the lab VM (22 days uptime) the gap was **absent**: v1 ≈ v2, every counter flat, `FilePmdMapped = 0`,
`do_set_pmd` never fired, and anonymous THP also refused to engage (`AnonHugePages = 0` despite
THP=`always`, `MADV_COLLAPSE` → `EINVAL`). Those are the classic signatures of **fragmented memory** —
the kernel cannot find 2 MiB-contiguous free pages, so the huge mapping (which needs them) silently
falls back to 4 KiB PTEs. We initially mis-attributed this to a kernel config
(`CONFIG_READ_ONLY_THP_FOR_FS` unset); the reproduced run **disproves** that — file PMD mapping works
here (`FilePmdMapped = 64 MiB`). A **reboot** defragmented memory and the gap appeared on the very
first read (+9.4 %). Lesson: THP-based effects depend on the *runtime memory state*, not just the
code — a long-running, fragmented system can hide them entirely.

## Evidence index
| File | Evidence |
|---|---|
| `task2/p10_reboot_confirmed.txt` | **gap reproduced (+9.4 %); dTLB 8.79M→18K; FilePmdMapped v2=64MiB, v1=0; LLC equal** |
| `task2/p5_folio_hist_{v1,v2}.txt` | folio-order histogram: v2 = 32× 2 MiB PMD folios; v1 = 16 KiB |
| `task2/p1b_filefrag.txt`, `p2_perfstat_*` | fragmentation ruled out (zero major faults / disk I/O) |
| `task2/p5c_force_thp.txt` | independent dTLB-lever magnitude (hugetlb): +12.7–20.2 % |
| `task2/p4_page_cache_ra_order.txt`, `p4_do_set_pmd.txt` | kernel source: folio order + PMD map |
| `task2/p8_*`, `p9_recheck.txt` | long-uptime VM: gap absent, all counters flat (the §7 detour) |
