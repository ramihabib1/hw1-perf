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

## Ruling out disk fragmentation

The obvious guess is that v1's small writes fragment the file on disk. But the file is only 64 MiB
and the VM has plenty of RAM, so after prepare the whole file sits in the page cache. perf stat
confirms this: during the run the page faults are all minor (page-faults equals minor-faults, zero
major faults), which means nothing is read from disk. If the disk is never touched, its layout
cannot affect the result. filefrag also shows both files are basically contiguous (2 extents vs 1).
So fragmentation is not the cause.

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

A handful of 2 MiB pages cover the whole file, so v2 almost never misses the TLB. The LLC
cache-misses are about the same for both (130M vs 127M), so this is a TLB effect and not a cache
effect. Fewer TLB misses means fewer page-table walks, and that is the ~9% speedup.

## Kernel source

- `mm/readahead.c`, `page_cache_ra_order`: the folio order is capped by the I/O size
  (`new_order = min(..., ilog2(ra->size))`). v2's 4 MiB writes allow a 2 MiB folio; v1's small
  writes do not.
- `mm/memory.c`, `do_set_pmd`: installs a 2 MiB huge-page mapping, but only when the folio is
  already PMD-sized. It fires for v2 and falls back to 4 KiB pages for v1.

## A note on reproducing it: the effect is fragile

The gap only appears on a freshly-booted VM, and it fades as the machine runs. When it is present,
v2's folios are 2 MiB and get PMD-mapped (`FilePmdMapped = 64 MiB`, `do_set_pmd` fires). When it has
faded, `do_set_pmd` is **never even called**. That is the key clue: `finish_fault` only calls
`do_set_pmd` when `folio_test_pmd_mappable(folio)` is true, i.e. when the folio is at least PMD-sized
(2 MiB). So in the faded state the folios are no longer 2 MiB at fault time, which is why the huge
mapping is never attempted and the gap disappears.

The trigger is therefore the system's **memory state**, which controls what order of folio v2's
write can build (and keep), not a kernel setting. I checked the call site directly: `do_set_pmd` is
gated by `folio_test_pmd_mappable`, not by `CONFIG_READ_ONLY_THP_FOR_FS` (which I had wrongly blamed
in an earlier draft). On a long-running, fragmented system v2's folios end up sub-2 MiB and the gap
vanishes; a reboot restores clean memory, the 4 MiB write produces 2 MiB folios again, and the gap
returns. I did not fully isolate which aspect of the aged memory state pushes the folio below 2 MiB,
so I report what is verified: the gap tracks whether v2's folio is PMD-sized at fault time.
