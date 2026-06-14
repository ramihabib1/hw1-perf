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

## Phase 4 — Kernel-source confirmation (/usr/src): folio-order cap + PMD condition
Explain (a) why v2's writes build larger folios, (b) why they stay sub-PMD, (c) why no huge mapping.
```
F=$(ls -d /usr/src/linux-* 2>/dev/null | head -1); echo "src=$F"
# (a/b) folio order selection + cap:
grep -n 'page_cache_ra_order\|MAX_PAGECACHE_ORDER\|mapping_max_folio' $F/mm/readahead.c $F/mm/filemap.c $F/include/linux/pagemap.h | head
awk '/page_cache_ra_order\(/{p=1} p{print} /^}/{if(p)exit}' $F/mm/readahead.c > task2/p4_page_cache_ra_order.txt
# (c) the PMD (huge) file-map path + its gate:
grep -n 'do_set_pmd' $F/mm/memory.c $F/mm/filemap.c | head
awk '/^vm_fault_t do_set_pmd\(|do_set_pmd\(struct/{p=1} p{print} /^}/{if(p)exit}' $F/mm/memory.c > task2/p4_do_set_pmd.txt
# (c) DECISIVE config check — is file-backed THP even compiled in?
zcat /proc/config.gz 2>/dev/null | grep -E 'READ_ONLY_THP_FOR_FS|TRANSPARENT_HUGEPAGE' || \
  grep -E 'READ_ONLY_THP_FOR_FS|TRANSPARENT_HUGEPAGE' /boot/config-$(uname -r) | tee task2/p4_kconfig_thp.txt
wc -l task2/p4_*.txt
```
Connect to the data: `page_cache_ra_order` decides folio order from the readahead/write size (why
v2's 4M writes → larger folios) and caps it (why ≤ a few pages here); `do_set_pmd` only installs a
huge PMD mapping when the folio is PMD-order AND aligned (why FilePmdMapped=0 with sub-PMD folios);
if `CONFIG_READ_ONLY_THP_FOR_FS` is **not set**, mmap'd ext4 reads can *never* get PMD file mappings
→ the dTLB-driven gap is structurally impossible on this kernel. **Student makes the final connection.**

## Phase 5 — Prove the mechanism (don't just assert it) + the histogram
Closes two audit gaps: (a) a real histogram, (b) confirm the dTLB/huge-page lever is actually
worth the gap on this hardware — the lecturer's "toggle the effect / measure again" (intro s.37).

### 5a — Folio-order histogram during prepare (the "histogram" data form)
```
COMMON="--file-num=1 --file-total-size=64M"
# find the right function/arg first:
sudo bpftrace -l 'kprobe:filemap_alloc_folio' ; sudo bpftrace -l 'tracepoint:filemap:*'
# folio order distribution (arg1 of filemap_alloc_folio is the order; verify & adjust):
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
sudo bpftrace -e 'kprobe:filemap_alloc_folio { @order_v1 = lhist(arg1,0,12,1); }' \
  -c "bash -lc 'cd ~/v1 && sysbench fileio $COMMON prepare'" 2>&1 | tee task2/p5_folio_hist_v1.txt
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
sudo bpftrace -e 'kprobe:filemap_alloc_folio { @order_v2 = lhist(arg1,0,12,1); }' \
  -c "bash -lc 'cd ~/v2 && sysbench fileio $COMMON --file-block-size=4M prepare'" 2>&1 | tee task2/p5_folio_hist_v2.txt
```
(If `filemap_alloc_folio` isn't probeable or arg1 isn't the order, report what `bpftrace -lv` shows
and use `__filemap_add_folio`/`folio_alloc` or the tracepoint's `order` field instead.)

### 5b — DECISIVE: is the dTLB/huge-page lever actually worth the gap here?
Same random 4 KiB reads over an anon mapping, THP ON vs OFF, measured by dTLB-misses + throughput.
```
cat > /tmp/thp_rndrd.c <<'EOF'
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <sys/mman.h>
int main(int argc, char **argv){
  size_t MB = argc>1?atol(argv[1]):64; int huge = argc>2?atoi(argv[2]):0;
  size_t sz = MB<<20;
  char *p = mmap(NULL,sz,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  if(p==MAP_FAILED){perror("mmap");return 1;}
  madvise(p,sz, huge?MADV_HUGEPAGE:MADV_NOHUGEPAGE);
  memset(p,1,sz);                 /* fault in (collapse to THP if huge) */
  size_t pages = sz/4096; uint64_t s=0,r=0x9e3779b97f4a7c15ULL;
  size_t iters=200000000;
  struct timespec a,b; clock_gettime(CLOCK_MONOTONIC,&a);
  for(size_t i=0;i<iters;i++){ r^=r<<13; r^=r>>7; r^=r<<17; s+=p[(r%pages)*4096]; }
  clock_gettime(CLOCK_MONOTONIC,&b);
  double t=(b.tv_sec-a.tv_sec)+(b.tv_nsec-a.tv_nsec)/1e9;
  fprintf(stderr,"MB=%zu huge=%d reads/s=%.0f sum=%lu\n",MB,huge,iters/t,(unsigned long)s);
  return 0;
}
EOF
gcc -O2 -o /tmp/thp_rndrd /tmp/thp_rndrd.c
{ for MB in 64 1024; do for h in 0 1; do
    echo "=== ${MB}MB huge=$h ==="
    sudo perf stat -e dTLB-load-misses,cycles,instructions /tmp/thp_rndrd $MB $h
  done; done; } 2>&1 | tee task2/p5_thp_dtlb_test.txt
# sanity: confirm THP actually mapped (AnonHugePages>0 for huge=1) — quick check:
grep -H AnonHugePages /proc/self/smaps_rollup 2>/dev/null | tee -a task2/p5_thp_dtlb_test.txt
```
Read: for huge=1 vs huge=0, do dTLB-load-misses drop sharply AND reads/s rise? If the rise is
~the 8–9% gap (and bigger at 1 GiB) ⇒ mechanism PROVEN: the gap is the huge-page dTLB win that
file-THP would deliver but `CONFIG_READ_ONLY_THP_FOR_FS=off` blocks. If no benefit ⇒ our dTLB
story is WRONG — report it, we rethink (maybe LLC/contiguity; also capture LLC-load-misses then).
Then `./scripts/vmsync "task2 p5 folio hist + THP dTLB proof"`, report files, STOP.

## Phase 5c — FORCE huge mapping and measure the dTLB win (fixes 5b)
5b's MADV_HUGEPAGE never engaged THP. This version FORCES it three ways and self-reports whether
it worked, so the dTLB contrast is real. Working set > LLC (256M/1G) to isolate TLB from cache.
```
cat > /tmp/thp_rndrd2.c <<'EOF'
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <errno.h>
#include <sys/mman.h>
#ifndef MADV_COLLAPSE
#define MADV_COLLAPSE 25
#endif
static long anonhuge_kb(void){
  FILE*f=fopen("/proc/self/smaps_rollup","r"); if(!f) return -1;
  char l[256]; long kb=-1;
  while(fgets(l,sizeof l,f)) if(sscanf(l,"AnonHugePages: %ld kB",&kb)==1) break;
  fclose(f); return kb;
}
int main(int argc,char**argv){
  size_t MB=argc>1?atol(argv[1]):256; int mode=argc>2?atoi(argv[2]):0; /* 0=4K 1=COLLAPSE 2=HUGETLB */
  size_t sz=MB<<20; int flags=MAP_PRIVATE|MAP_ANONYMOUS; if(mode==2) flags|=MAP_HUGETLB;
  char*p=mmap(NULL,sz,PROT_READ|PROT_WRITE,flags,-1,0);
  if(p==MAP_FAILED){ fprintf(stderr,"mmap(mode=%d) failed: %s\n",mode,strerror(errno)); return 1; }
  memset(p,1,sz);
  if(mode==1 && madvise(p,sz,MADV_COLLAPSE)!=0) fprintf(stderr,"MADV_COLLAPSE failed: %s\n",strerror(errno));
  fprintf(stderr,"mode=%d MB=%zu AnonHugePages_kB=%ld%s\n",mode,MB,anonhuge_kb(),mode==2?" (hugetlb: see HugePages_Free)":"");
  size_t pages=sz/4096; uint64_t s=0,r=0x9e3779b97f4a7c15ULL; size_t iters=200000000;
  struct timespec a,b; clock_gettime(CLOCK_MONOTONIC,&a);
  for(size_t i=0;i<iters;i++){ r^=r<<13; r^=r>>7; r^=r<<17; s+=p[(r%pages)*4096]; }
  clock_gettime(CLOCK_MONOTONIC,&b);
  double t=(b.tv_sec-a.tv_sec)+(b.tv_nsec-a.tv_nsec)/1e9;
  fprintf(stderr,"mode=%d MB=%zu reads/s=%.0f sum=%lu\n",mode,MB,iters/t,(unsigned long)s);
  return 0;
}
EOF
gcc -O2 -o /tmp/thp_rndrd2 /tmp/thp_rndrd2.c
# base (4K) vs MADV_COLLAPSE (2M THP) — watch AnonHugePages_kB in the output (must be >0 for mode 1):
{ for MB in 256 1024; do for m in 0 1; do
    echo "=== ${MB}MB mode=$m ==="
    sudo perf stat -e dTLB-load-misses,cycles,instructions /tmp/thp_rndrd2 $MB $m
  done; done; } 2>&1 | tee task2/p5c_force_thp.txt
# guaranteed huge via hugetlb — reserve, run, release:
echo 600 | sudo tee /proc/sys/vm/nr_hugepages >/dev/null
grep -E 'HugePages_(Total|Free)' /proc/meminfo | tee -a task2/p5c_force_thp.txt
{ for MB in 256 1024; do
    echo "=== ${MB}MB mode=2 (MAP_HUGETLB) ==="
    sudo perf stat -e dTLB-load-misses,cycles,instructions /tmp/thp_rndrd2 $MB 2
  done; } 2>&1 | tee -a task2/p5c_force_thp.txt
echo 0 | sudo tee /proc/sys/vm/nr_hugepages >/dev/null   # release as-shipped
```
Read: for the runs where huge mapping ENGAGED (AnonHugePages_kB>0 for mode 1, or HugePages used for
mode 2), do dTLB-load-misses drop sharply vs mode 0, and reads/s rise? The size of that rise is the
dTLB-win magnitude on this CPU — the number proving the Phase-4/5 claim. Report AnonHugePages_kB and
HugePages_Free so we KNOW which runs actually got huge pages. Then vmsync "task2 p5c", report, STOP.

## Phase 7 — Reproduce the mechanism on a REAL file mapping (tmpfs/shmem huge pages)
ext4 file-THP is compiled out (READ_ONLY_THP_FOR_FS unset), but tmpfs/shmem huge pages are a
SEPARATE path with a runtime knob — NOT compiled out. Use it to get a genuine PMD-mapped *file*
mmap (upgrades the §5b anonymous hugetlb proxy to an actual file). Honest scope: this reproduces the
folio→PMD-map→dTLB MECHANISM on a file, NOT the literal ext4 v1/v2 gap.
```
COMMON="--file-num=1 --file-total-size=64M"
RUN="sysbench fileio $COMMON --file-test-mode=rndrd --file-io-mode=mmap --file-block-size=4K --time=5 run"
cat /sys/kernel/mm/transparent_hugepage/shmem_enabled | tee task2/p7_shmem_env.txt
sudo mkdir -p /mnt/hugetmp
```
### 7a — tmpfs huge=always (PMD file mapping should engage)
```
sudo mount -t tmpfs -o huge=always,size=2G tmpfs /mnt/hugetmp
sudo chown $USER /mnt/hugetmp; mkdir -p /mnt/hugetmp/v2
( cd /mnt/hugetmp/v2 && sysbench fileio $COMMON --file-block-size=4M prepare ) >/dev/null 2>&1
( cd /mnt/hugetmp/v2 && $RUN ) & sleep 2
grep -E 'Pmd|Huge|Rss|Anon' /proc/$(pgrep -n sysbench)/smaps_rollup ; wait
( cd /mnt/hugetmp/v2 && perf stat -e dTLB-load-misses,cycles,instructions $RUN ) 2>&1
```
all of the above → `2>&1 | tee task2/p7_shmem_huge.txt`
### 7b — same tmpfs, huge=never (clean on/off toggle)
```
sudo umount /mnt/hugetmp
sudo mount -t tmpfs -o huge=never,size=2G tmpfs /mnt/hugetmp
sudo chown $USER /mnt/hugetmp; mkdir -p /mnt/hugetmp/v2
( cd /mnt/hugetmp/v2 && sysbench fileio $COMMON --file-block-size=4M prepare ) >/dev/null 2>&1
( cd /mnt/hugetmp/v2 && $RUN ) & sleep 2
grep -E 'Pmd|Huge|Rss|Anon' /proc/$(pgrep -n sysbench)/smaps_rollup ; wait
( cd /mnt/hugetmp/v2 && perf stat -e dTLB-load-misses,cycles,instructions $RUN ) 2>&1
```
all of the above → `2>&1 | tee task2/p7_shmem_base.txt`, then `sudo umount /mnt/hugetmp`.
PREDICT FIRST (ShmemPmdMapped, dTLB-misses, reads/s for each). Discriminating read:
- huge=always: ShmemPmdMapped>0, dTLB-misses collapse ~8.7M→thousands, reads/s rise → MECHANISM
  reproduced on a file mapping. (Do NOT relabel as "the ext4 gap reproduced".)
- huge=always ALSO shows no PMD → second data point that THP is broken on this image
  (MADV_COLLAPSE gave EINVAL, anon THP refused at [always]) → strengthens the wrong-image argument.
Then `./scripts/vmsync "task2 p7 shmem file-THP"`, report, STOP.

## Cleanup
```
( cd ~/v1 && sysbench fileio $COMMON cleanup ); ( cd ~/v2 && sysbench fileio $COMMON cleanup )
```
