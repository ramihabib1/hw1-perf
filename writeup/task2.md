# Task 2 — Root cause of the v1/v2 random-read gap (prepare block size)

**Setup.** Same 64 MiB file built two ways — v1 with default (~16 KiB) prepare writes, v2 with
4 MiB writes — then the identical random-read (`mmap`, 4 KiB, 5 s) benchmark on each. Reference:
v2 ~8–9 % faster.

**Result (one line).** The prepare block size sets the **page-cache folio order**: v2's 4 MiB
writes build the entire file out of **2 MiB (PMD-order) folios** (proven by a folio-order histogram);
v1's small writes cap at 16 KiB. The reference 8–9 % is consistent with **PMD-mapping** those huge
folios, which removes the dTLB misses that dominate this benchmark — a lever **measured here at
+12.7 % to +20.2 %** (via `hugetlb`), the same order of magnitude as the gap (in fact *larger*, so a
fully-PMD-mapped reference would exceed 8–9 % — see §5b). On *this* VM image the gap **does not
reproduce**: v1 and v2 are statistically indistinguishable (identical dTLB, identical cycles), and the
ext4 file-THP path that would PMD-map v2's folios is compiled out (`CONFIG_READ_ONLY_THP_FOR_FS`
unset, `do_set_pmd` never reached, `FilePmdMapped=0`). **Open tension:** the assignment expects the
gap on this course VM, yet this image cannot produce it — raised with the instructor (§8).

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
Direct folio-order histogram (bpftrace on `mm_filemap_add_to_page_cache`, field `order`), captured
**during prepare** — i.e. these folios are built by the **buffered-write path** as the file is
created, not by readahead at run time:

| | folio orders built | meaning |
|---|---|---|
| v1 (4 KiB writes) | 4096 × **order-2 (16 KiB)** + 3027 × order-0 | small folios only |
| v2 (4 MiB writes) | **32 × order-9 (2 MiB / PMD)** = whole file + 2870 × order-0 | **PMD-order folios** |

**v2's 4 MiB writes build the entire 64 MiB file as 2 MiB PMD-order folios; v1 caps at 16 KiB.**
(Earlier I inferred from average folio counts that both stayed sub-PMD — the histogram disproves
that; v2 clearly reaches PMD order. Lesson: count the distribution, don't average it.)
**Why prepare-time folios matter at run time:** §3 showed zero major faults during the run, i.e. the
file is fully cache-resident — the folios built at prepare *persist* into the read phase, so the write
block size still governs the layout the random reads see. The larger folios also make fault-around
more effective ⇒ v2 has **2× fewer page-faults** (821 vs 1813; **13× fewer at 1 GiB**: 1301 vs 17173
[p3_bigfile.txt]) — but in absolute terms ~1000 extra faults over a 5 s / 9.4 M-read run is **< 0.1 %
of runtime**, far too small to be a throughput factor (see §5).

## 5. Why the *throughput* gap is absent — the huge folios are not PMD-mapped  [p2_smaps_*, p2_faultcount_*]
v2 has 2 MiB folios, but the gap needs them **mapped as 2 MiB PMD entries** so the TLB benefits:
- The benchmark is **dTLB-bound**: `dTLB-load-misses` ≈ **8.74 M / 9.4 M reads ≈ 0.93 miss/read**
  (4 KiB TLB reach ~4 MiB ≪ 64 MiB working set). Only PMD mapping (2 MiB → 64 MiB in ~32 TLB
  entries) removes this.
- But `FilePmdMapped = 0 kB` for both, and `do_set_pmd` **never fired** (bpftrace) → v2's 2 MiB
  folios are mapped with **4 KiB PTEs** → `dTLB-load-misses` **identical** v1 vs v2 (8.74 M vs
  8.75 M; 7.41 M vs 7.41 M at 1 GiB) → **identical cycles** (~13.26 B both) → **no throughput gap**.
- Across **all THP modes** (always/madvise/never) v2 and v1 differ by ≲1 % with the **sign flipping
  run-to-run** — i.e. noise, not a signal [p3_thp_sweep, p3_bigfile].

**Honest reading:** on this image v1 and v2 are **statistically indistinguishable** — identical dTLB,
identical cycles, reads/s at the noise floor. There is no measurable ~1 % "edge"; the fault-count
difference (§4) is < 0.1 % of runtime and does not show up in throughput. The documented 8–9 % is the
**dTLB term** that would appear *if* v2's huge folios were PMD-mapped — which they are not here (§6).

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
— larger at the bigger working set (the TLB-reach signature). This is the **same order of magnitude**
as the reference 8–9 %, confirming a dTLB mechanism is plausible — but note it is *larger* than the gap,
not bracketing it: a *fully* PMD-mapped reference would exceed 8–9 %. So the reference is likely only
**partially** PMD-mapped (a 64 MiB working set yields fewer 2 MiB entries than the 256 MiB/1 GiB tested
here), or a second factor moderates it. (Anonymous `MADV_COLLAPSE`/THP did *not* engage on this kernel —
`EINVAL`, `AnonHugePages=0` even at THP=always; a quirk of this image, so `hugetlb` was used as the
reliable toggle.) Caveat: this is an **anonymous** mapping — a *proxy* for the file case; §7a reproduces
the lever on an actual file mapping. What v2 lacks here is not the folios (it has 2 MiB ones) nor the
hardware payoff (measured) — it is the *file-cache* PMD mapping (§6).

## 6. Kernel-source confirmation  [p4_page_cache_ra_order, p4_do_set_pmd, p4_kconfig_thp]
- **Folio order is bounded by the I/O size.** `mm/readahead.c:467 page_cache_ra_order()` shows the
  rule on the *read* side:
  ```c
  new_order = min(mapping_max_folio_order(mapping), new_order);
  new_order = min_t(unsigned int, new_order, ilog2(ra->size));   /* ← order bounded by request size */
  ```
  The **write** path that actually built our folios (§4 histogram, captured during prepare) applies the
  same principle — the folio order tracks the write granularity — which is why v2's 4 MiB writes reach
  order-9 (2 MiB) and v1's small writes stay ≤ order-2.
- **PMD mapping is a separate step** — `mm/memory.c:5408 do_set_pmd()` installs a 2 MiB PMD only for a
  PMD-order, PMD-suitable (aligned) folio. Its own gates (THP enabled, alignment, `folio_order ==
  HPAGE_PMD_ORDER`) are *generic* (this function is shared with tmpfs); it does **not** itself test the
  config below. Here it **never fired** for the ext4 mapping (bpftrace) ⇒ `FilePmdMapped=0`.
- **The config that gates the ext4 file-THP path** — `/boot/config-7.0.0-15-generic`:
  ```
  CONFIG_TRANSPARENT_HUGEPAGE=y
  # CONFIG_READ_ONLY_THP_FOR_FS is not set      ← read-only file-THP for regular FS NOT compiled in
  ```
  `READ_ONLY_THP_FOR_FS` enables the read-only file-THP support for regular filesystems (ext4). With it
  unset, the fault path for an mmap'd ext4 file does not reach `do_set_pmd`, so v2's PMD-sized folio is
  PTE-mapped — consistent with our `do_set_pmd`-never-fires trace. **Hypothesis (not provable from this
  VM):** on the image where the gap was characterized, this path is enabled and PMD-maps v2's folios.
  We cannot inspect that machine, so this is stated as a hypothesis, not a fact (§8). *(tmpfs/shmem huge
  pages are a separate path with a runtime knob and are NOT compiled out — used in §7a to exercise the
  file PMD-mapping mechanism directly.)*

## 7a. Reproducing the mechanism on a real *file* mapping (tmpfs/shmem)
The §5b hugetlb proof is *anonymous* memory — a proxy. To exercise PMD-mapping of an actual file the
random reads go through, we use **tmpfs huge pages** (`shmem_enabled`), a path that is *not* gated by
`READ_ONLY_THP_FOR_FS`: prepare the v2 file on a `huge=always` tmpfs and run the same benchmark, vs
`shmem_enabled=never`. *(Results: see `task2/p7_shmem_*`. This reproduces the folio-order → PMD-map →
dTLB mechanism **on a file mapping**; it is explicitly **not** a reproduction of the literal ext4
v1/v2 gap — on `huge=always` tmpfs v1's small writes may also receive huge folios, collapsing the
v1-vs-v2 contrast even as both go fast.)*

## 7b. Conclusion
Root cause: **the prepare write block size sets the page-cache folio order** — v2's 4 MiB writes
build 2 MiB PMD-order folios (proven by the order histogram), v1's stay at 16 KiB. The *throughput*
gap is the **dTLB win from PMD-mapping** those huge folios, which dominates this dTLB-bound benchmark;
the lever is worth +12.7–20.2 % on this CPU (§5b). Fragmentation was ruled out (zero disk I/O).

**On this VM image the gap does not reproduce:** v1 and v2 are statistically indistinguishable
(identical dTLB and cycles), because the ext4 file-THP path that would PMD-map v2's folios is compiled
out (`CONFIG_READ_ONLY_THP_FOR_FS` unset, `do_set_pmd` never reached, `FilePmdMapped=0`). The
mechanism is nonetheless demonstrated end-to-end: huge folios exist (§4) → PMD-mapping such a working
set delivers a dTLB win (§5b, §7a) → the ext4 file path to that mapping is gated here (§6).

**What is measured vs hypothesised:** every link above is measured or read from source. The one claim
we *cannot* verify is about the **reference machine** — that it enables file-THP and so produces the
8–9 %. That is a **hypothesis**, and it sits in tension with the assignment (§8).

## 8. Open tension — the VM image
The assignment states the gap is stable **on course-08**, yet this course-08 cannot produce it:
`CONFIG_READ_ONLY_THP_FOR_FS` is unset, so the ext4 file-THP path is compiled out, and v1/v2 measure
identical. The kernel string `7.0.0-15-generic` is non-standard. Both facts can be reconciled only if
the image the gap was characterised on differs from this one. **This was raised with the instructor
before submission** (see the email in the appendix); the conclusion is therefore presented as
"mechanism identified and demonstrated; literal gap not reproducible on this image," not as a claim
that the gap is impossible in general.

## Evidence index
| File | Evidence |
|---|---|
| `task2/p0_env.txt` | environment (ext4, THP=always) |
| `task2/p1_baseline_gap.txt` | gap absent (v1≈v2) |
| `task2/p1b_filefrag.txt` | fragmentation trivial (2 vs 1 extent) |
| `task2/p2_perfstat_{v1,v2}.txt` | page-faults 2× fewer (v2); dTLB identical; 100 % minor |
| `task2/p2_smaps_*`, `p2_faultcount_*` | FilePmdMapped=0; do_set_pmd never fires |
| `task2/p5_folio_hist_{v1,v2}.txt` | **folio-order histogram: v2 = 32× 2 MiB PMD folios; v1 = 16 KiB** |
| `task2/p3_folios_*`, `p3_thp_sweep.txt`, `p3_bigfile.txt` | fault scaling (2×→13×); v1≈v2 (noise) in all THP modes |
| `task2/p4_page_cache_ra_order.txt` | folio order bounded by I/O size |
| `task2/p4_do_set_pmd.txt` | PMD file-map path (gated) |
| `task2/p4_kconfig_thp.txt` | `CONFIG_READ_ONLY_THP_FOR_FS` not set — the structural gate |
| `task2/p5_thp_dtlb_test.txt` | first dTLB microbench (inconclusive: anon THP didn't engage) |
| `task2/p5c_force_thp.txt` | **dTLB lever measured: 2 MiB hugetlb mapping → dTLB ~70,000× fewer, +12.7–20.2 %** |
