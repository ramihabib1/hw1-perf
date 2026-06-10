# Task 2 Runbook — v2 (4 MiB prepare) random-read ~8–9% faster

> Run on `course-08` under: `script -f ~/hw1/logs/task2_$(date +%Y%m%d_%H%M).log`
> **PREDICT in notes.md before every measured run.**

```
COMMON="--file-num=1 --file-total-size=64M"
RUN="sysbench fileio $COMMON --file-test-mode=rndrd --file-io-mode=mmap --file-block-size=4K --time=5 run"
```

## Phase 0 — Environment (capture once)  → task2/p0_env.txt
```
{ uname -a; echo; free -h; echo; systemd-detect-virt; echo;
  mount | grep -E "$(stat -c %m ~)"; echo;       # which fs is $HOME on? (ext4? xfs?)
  echo "THP:"; cat /sys/kernel/mm/transparent_hugepage/enabled;
  echo "sysbench:"; sysbench --version; } 2>&1 | tee task2/p0_env.txt
```

## Phase 1 — Setup + baseline (confirm the gap is real and stable)  → task2/p1_baseline_gap.txt
```
mkdir -p ~/v1 ~/v2
( cd ~/v1 && sysbench fileio $COMMON prepare )
( cd ~/v2 && sysbench fileio $COMMON --file-block-size=4M prepare )
# n>=5 each, interleaved, to separate gap from run-to-run noise:
for i in $(seq 5); do
  echo "--- iter $i v1 ---"; ( cd ~/v1 && $RUN ) | grep -E 'reads/s|read, MiB';
  echo "--- iter $i v2 ---"; ( cd ~/v2 && $RUN ) | grep -E 'reads/s|read, MiB';
done 2>&1 | tee task2/p1_baseline_gap.txt
```
Gap must be stable and > noise. Note: everything that differs was baked in at PREPARE time.

## Phase 1B — Diagnose the MISSING gap (the gap did not reproduce under THP=always)
The Phase 1 baseline showed v1 ≈ v2 (no 8–9% gap). Before anything else, find out why.

### Sanity — were the two files actually prepared differently?
```
filefrag ~/v1/test_file.0 ~/v2/test_file.0 2>&1 | tee task2/p1b_filefrag.txt
```
(Expect v1 to have many more extents than v2. Confirms the prepare block-size difference took.)

### Decisive — sweep THP and see if the gap appears (toggle-the-effect)
Suspect: THP=always gives large folios to BOTH files, erasing the write-size difference.
For each THP mode, drop caches, re-prepare under that mode, re-run:
```
COMMON="--file-num=1 --file-total-size=64M"
RUN="sysbench fileio $COMMON --file-test-mode=rndrd --file-io-mode=mmap --file-block-size=4K --time=5 run"
for thp in always madvise never; do
  echo $thp | sudo tee /sys/kernel/mm/transparent_hugepage/enabled >/dev/null
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  rm -rf ~/v1 ~/v2; mkdir -p ~/v1 ~/v2
  ( cd ~/v1 && sysbench fileio $COMMON prepare ) >/dev/null 2>&1
  ( cd ~/v2 && sysbench fileio $COMMON --file-block-size=4M prepare ) >/dev/null 2>&1
  echo "=== THP=$thp ==="
  for i in 1 2 3; do
    printf 'v1 '; ( cd ~/v1 && $RUN ) | grep -oP 'reads/s:\s*\K[0-9.]+'
    printf 'v2 '; ( cd ~/v2 && $RUN ) | grep -oP 'reads/s:\s*\K[0-9.]+'
  done
done 2>&1 | tee task2/p1b_thp_sweep.txt
echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled >/dev/null   # restore as-shipped
```
Read: does a v2>v1 gap (~8–9%) appear under `madvise` and/or `never` but not `always`?
That localizes the effect to THP/folio size. If NO gap appears under any mode, the mechanism
is something else and we keep digging.

## Phase 2 — Diagnose with the LECTURER'S technique (caching deck slides 58, 64–65)
His Redis example: headline counter = **page-faults**; then **count huge-page usage** on the
fault path with bpftrace ("count, don't eyeball"). We apply the same to the file page cache.

### 2a — perf stat, page-faults FIRST (his slide 58 comparison)
```
( cd ~/v1 && perf stat -e page-faults,minor-faults,dTLB-load-misses,cycles,instructions $RUN ) 2>&1 | tee task2/p2_perfstat_v1.txt
( cd ~/v2 && perf stat -e page-faults,minor-faults,dTLB-load-misses,cycles,instructions $RUN ) 2>&1 | tee task2/p2_perfstat_v2.txt
```
Read: does v2 have FEWER page-faults than v1? (instructions ~equal.) Equal page-faults ⇒
consistent with the missing gap ⇒ go to 2b to learn whether huge pages are used for both/neither.

### 2b — Are huge pages / large folios actually used? (his VM_FAULT_FALLBACK count, slides 64–65)
Direct view — huge file mappings while each run is in flight:
```
( cd ~/v1 && $RUN ) & sleep 2; grep -E 'File|Pmd|Huge|Rss' /proc/$(pgrep -n sysbench)/smaps_rollup | tee task2/p2_smaps_v1.txt; wait
( cd ~/v2 && $RUN ) & sleep 2; grep -E 'File|Pmd|Huge|Rss' /proc/$(pgrep -n sysbench)/smaps_rollup | tee task2/p2_smaps_v2.txt; wait
```
Count the PMD (huge) vs base-page file faults on the read path (analogous to his fallback count):
```
BT='kprobe:do_set_pmd { @pmd_huge = count(); } kprobe:set_pte_range { @base = count(); }'
( cd ~/v1 && $RUN ) & sleep 1; sudo bpftrace -e "$BT" -c "sleep 3" 2>&1 | tee task2/p2_faultcount_v1.txt; wait
( cd ~/v2 && $RUN ) & sleep 1; sudo bpftrace -e "$BT" -c "sleep 3" 2>&1 | tee task2/p2_faultcount_v2.txt; wait
```
(If `do_set_pmd`/`set_pte_range` aren't probeable on this kernel, fall back to counting
`filemap_fault` and tracing folio order; report what names exist via `grep`.)
PREDICT first: are huge pages used for v2 but not v1, or for both, or neither? That answers
WHY the gap is (or isn't) present.

## Phase 3 — Rule out the seductive wrong answer: disk fragmentation
v1's small writes fragment the file; v2's 4M writes don't. But is the run even touching disk?
```
filefrag -v ~/v1/test_file.0 | tail -3       # extent count v1
filefrag -v ~/v2/test_file.0 | tail -3       # extent count v2  (expect far fewer)
# Are both fully in page cache during the run? If yes, disk layout is irrelevant:
vmtouch ~/v1/test_file.0 ~/v2/test_file.0    2>/dev/null || \
  ( cd ~/v1 && perf stat -e block:block_rq_issue $RUN )   # ~0 block I/O => cache-resident
```
Write the explicit ruling-out in notes.md WITH the data (extent counts + zero block I/O).

## Phase 3b — Confirm the real mechanism: folio order in the page cache
The persistent prepare-time difference is the size of the folios the page cache built.
```
# folio sizes mapped for the file (look for FilePmdMapped / large mappings):
grep -E 'File|Anon|Pmd' /proc/$(pgrep -n sysbench)/smaps_rollup 2>/dev/null   # during a run
# or trace folio allocation order during prepare:
sudo bpftrace -e 'kprobe:__filemap_add_folio { @order = hist(arg3); }'        # adjust arg per kernel
# (run a v1 prepare vs a v2 prepare under this; compare the order histograms)
```

## Phase 3 — Confirm folio order, find the code, try to reproduce the gap
Phase 2 showed: v2 builds larger folios (2× fewer faults) but both stay sub-PMD ⇒ no huge
mapping ⇒ no dTLB win ⇒ no throughput gap. Now: prove the folio sizes, locate the kernel
logic, and try to push folios to PMD so the gap appears (toggle-the-effect, like MALLOC_TOP_PAD).

### 3a — Confirm folio order built during prepare (bpftrace)
```
BT='tracepoint:filemap:mm_filemap_add_to_page_cache { @folios = count(); }'
COMMON="--file-num=1 --file-total-size=64M"
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
sudo bpftrace -e "$BT" -c "bash -lc 'cd ~/v1 && sysbench fileio $COMMON prepare'" 2>&1 | tee task2/p3_folios_v1.txt
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
sudo bpftrace -e "$BT" -c "bash -lc 'cd ~/v2 && sysbench fileio $COMMON --file-block-size=4M prepare'" 2>&1 | tee task2/p3_folios_v2.txt
```
16384 pages / @folios = avg pages per folio. Expect v2 ≫ v1, both < 512 (PMD).

### 3b — Reproduction attempts (make v2 reach PMD ⇒ make the gap appear)
```
RUN="sysbench fileio $COMMON --file-test-mode=rndrd --file-io-mode=mmap --file-block-size=4K --time=5 run"
# (i) THP sweep — does any mode change FilePmdMapped / the gap? (the deferred sweep)
for thp in always madvise never; do
  echo $thp | sudo tee /sys/kernel/mm/transparent_hugepage/enabled >/dev/null
  sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
  rm -rf ~/v1 ~/v2; mkdir -p ~/v1 ~/v2
  ( cd ~/v1 && sysbench fileio $COMMON prepare ) >/dev/null 2>&1
  ( cd ~/v2 && sysbench fileio $COMMON --file-block-size=4M prepare ) >/dev/null 2>&1
  echo "=== THP=$thp ==="; for i in 1 2 3; do
    printf 'v1 '; ( cd ~/v1 && $RUN ) | grep -oP 'reads/s:\s*\K[0-9.]+';
    printf 'v2 '; ( cd ~/v2 && $RUN ) | grep -oP 'reads/s:\s*\K[0-9.]+'; done
done 2>&1 | tee task2/p3_thp_sweep.txt
echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled >/dev/null
# (ii) bigger working set — larger files may reach higher-order/PMD folios
BIG="--file-num=1 --file-total-size=1G"
BRUN="sysbench fileio $BIG --file-test-mode=rndrd --file-io-mode=mmap --file-block-size=4K --time=5 run"
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
rm -rf ~/b1 ~/b2; mkdir -p ~/b1 ~/b2
( cd ~/b1 && sysbench fileio $BIG prepare ) >/dev/null 2>&1
( cd ~/b2 && sysbench fileio $BIG --file-block-size=4M prepare ) >/dev/null 2>&1
{ echo "=== 1G v1 ==="; ( cd ~/b1 && $BRUN ) | grep -E 'reads/s|read, MiB';
  ( cd ~/b1 && perf stat -e page-faults,dTLB-load-misses $BRUN ) 2>&1 | grep -E 'page-faults|dTLB';
  echo "=== 1G v2 ==="; ( cd ~/b2 && $BRUN ) | grep -E 'reads/s|read, MiB';
  ( cd ~/b2 && perf stat -e page-faults,dTLB-load-misses $BRUN ) 2>&1 | grep -E 'page-faults|dTLB';
} 2>&1 | tee task2/p3_bigfile.txt
( cd ~/b1 && sysbench fileio $BIG cleanup ); ( cd ~/b2 && sysbench fileio $BIG cleanup )
```
Read: does a v2>v1 reads/s gap appear under any THP mode or at 1 GiB, with FilePmdMapped>0 /
fewer dTLB-misses? If yes → gap reproduced and tied to huge mapping. If never → gap is genuinely
dormant in this kernel; we explain it via the sub-PMD folio cap + source.

## Phase 4 — Kernel-source confirmation (/usr/src)
- Readahead/folio order: `mm/readahead.c` (`page_cache_ra_order`), `mm/filemap.c` (`filemap_fault`, `filemap_map_pages`)
- Large folios in the page cache + how mmap maps them (TLB consequence)
Read the order-selection logic; connect it to the `perf stat` counter that differed. **Your call to make.**

## Cleanup
```
( cd ~/v1 && sysbench fileio $COMMON cleanup ); ( cd ~/v2 && sysbench fileio $COMMON cleanup )
```
