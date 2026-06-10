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

## Phase 2 — The discriminating probe: WHERE do the cycles go?
Same data, same run command → the difference lives in the per-access cost. Find it.
```
( cd ~/v1 && perf stat -e cycles,instructions,minor-faults,dTLB-load-misses,LLC-load-misses $RUN ) 2>&1 | tee perfstat_v1.txt
( cd ~/v2 && perf stat -e cycles,instructions,minor-faults,dTLB-load-misses,LLC-load-misses $RUN ) 2>&1 | tee perfstat_v2.txt
```
PREDICT first: which counter, if any, differs between v1 and v2? That counter IS the mechanism.

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

## Phase 4 — Kernel-source confirmation (/usr/src)
- Readahead/folio order: `mm/readahead.c` (`page_cache_ra_order`), `mm/filemap.c` (`filemap_fault`, `filemap_map_pages`)
- Large folios in the page cache + how mmap maps them (TLB consequence)
Read the order-selection logic; connect it to the `perf stat` counter that differed. **Your call to make.**

## Cleanup
```
( cd ~/v1 && sysbench fileio $COMMON cleanup ); ( cd ~/v2 && sysbench fileio $COMMON cleanup )
```
