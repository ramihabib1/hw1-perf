# Task 2: Two prepares, one run

## The phenomenon

I create the same 64 MiB file twice: v1 with the default (~16 KiB) write block size, and v2 with
4 MiB writes. Then I run the same random 4 KiB mmap read test against each. v2 comes out about 9%
faster:

| | reads/s |
|---|---|
| v1 | 1,942,670 |
| v2 | 2,124,442 (+9.4%) |

Both files hold the same data and the read command is identical, so the only difference is how
each file was written during prepare.

## How I approached it

The difference has to come from how each file was written, so I considered three possible causes
and tested each: disk fragmentation, CPU cache (LLC) behaviour, and how the kernel stores the file
in the page cache. I ruled out the first two with counters and traced the third to the mechanism.

## Ruling out disk fragmentation

The obvious guess is that v1's small writes fragment the file on disk. But the file is only 64 MiB
and the VM has plenty of RAM, so after prepare the whole file sits in the page cache. perf stat
confirms this: during the run the page faults are all minor (page-faults equals minor-faults, zero
major faults), which means nothing is read from disk. If the disk is never touched, its layout
cannot affect the result. filefrag also shows both files are basically contiguous (2 extents vs 1).
So fragmentation is not the cause.

## Ruling out the CPU cache

The next guess is that v2 has better last-level-cache behaviour. If so it would show fewer
cache-misses. perf stat shows the opposite: cache-misses are about equal, 130.6M for v1 and 126.8M
for v2, at similar cache-references. So the speedup is not a cache effect, which is what later points
me at the TLB instead.

## What actually differs: folio size

The real difference is how the kernel stores each file in the page cache. I traced the folio sizes
built during prepare with bpftrace (on `mm_filemap_add_to_page_cache`):

| | folios in the page cache |
|---|---|
| v1 (16 KiB writes) | 16 KiB folios |
| v2 (4 MiB writes) | 2 MiB folios (the whole file) |

Bigger writes let the kernel build bigger folios.

## Why bigger folios read faster

v2's 2 MiB folios get mapped as huge pages. `/proc/<pid>/smaps_rollup` during a run shows:

| | FilePmdMapped |
|---|---|
| v1 | 0 |
| v2 | 65,536 kB (the whole 64 MiB file) |

This matters because the benchmark is limited by the TLB. With 4 KiB pages the TLB only covers a
few MiB, so random reads across a 64 MiB file miss almost every time. perf stat shows exactly that:

| | dTLB-load-misses |
|---|---|
| v1 | 8,790,240 |
| v2 | 18,488 (about 475x fewer) |

A handful of 2 MiB pages cover the whole file, so v2 almost never misses the TLB. Fewer TLB misses
means fewer page-table walks, and that is the ~9% speedup.

## Checking the size of the effect independently

To confirm that 2 MiB mapping really is worth this much on this CPU, I ran the same random 4 KiB
read pattern over a mapping backed by explicit 2 MiB pages (MAP_HUGETLB) versus normal 4 KiB pages.
The dTLB misses dropped from about 195M to a few thousand, and throughput rose +12.7% on a 256 MiB
working set and +20.2% on 1 GiB. That is the same order as the file gap, and it isolates the effect
to the page size alone, so the mechanism is not specific to the file path.

## Kernel source

- `mm/readahead.c`, `page_cache_ra_order`: the folio order is capped by the I/O size
  (`new_order = min(..., ilog2(ra->size))`). v2's 4 MiB writes allow a 2 MiB folio; v1's small
  writes do not.
- `mm/memory.c`, `do_set_pmd`: installs a 2 MiB huge-page mapping, but only when the folio is
  already PMD-sized. It fires for v2 and falls back to 4 KiB pages for v1.

## Summary

prepare block size (4 MiB vs 16 KiB) sets the page-cache folio order (2 MiB vs 16 KiB). v2's 2 MiB
folios are mapped as huge pages, which cuts the dTLB misses that dominate this random-read workload
(8.79M down to 18K), and that is the ~9% speedup. It is a TLB effect, not a cache effect, and not a
disk effect.
