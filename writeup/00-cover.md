# Performance Engineering (2360013) — Assignment 1

Rami Habib, ID 325420180. Instructor: Nadav Amit.

VM: course-08, a KVM guest with an Intel Xeon Gold 5420+, 4 vCPUs, kernel 7.0.0-15, ext4.

Short version of my findings:

- **Task 1:** the pipe gets faster under CPU load because the scheduler co-locates the two
  pipe processes on one core. With idle CPUs around it spreads them across cores, and every
  round-trip then pays a cross-core wakeup; load removes the idle CPUs so they end up together.
- **Task 2:** v2 reads faster because its 4 MiB writes make the kernel store the file in 2 MiB
  folios, which get mapped as huge pages. That cuts TLB misses by about 475x on a workload that
  is otherwise TLB-bound.

The raw command outputs I relied on are in the appendix.
