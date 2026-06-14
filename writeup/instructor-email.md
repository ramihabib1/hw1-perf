# Draft email to instructor (re: Task 2 VM image)

**To:** Nadav Amit <namit@technion.ac.il>
**Subject:** HW1 Task 2 — course-08 has CONFIG_READ_ONLY_THP_FOR_FS unset (v1/v2 gap doesn't reproduce)

Hi Nadav,

While working on Assignment 1, Task 2, I could not reproduce the documented v1/v2
random-read gap on my VM (course-08). Both runs are statistically identical:

- v1 ≈ 1,933,000 reads/s, v2 ≈ 1,931,000 reads/s (the assignment expects v2 ≈ 2,080,000)
- identical dTLB-load-misses (8.74M vs 8.75M) and identical cycles between the two

Investigating, I found the mechanism but also a likely cause for the non-reproduction:

- The v2 (4 MiB prepare) file *does* build 2 MiB PMD-order page-cache folios and v1 caps
  at 16 KiB (confirmed with a folio-order histogram), so the prepare-block-size → folio-size
  link is real.
- The 8–9 % speedup should come from PMD-*mapping* those folios (it removes the dTLB misses
  that dominate this benchmark — I measured that lever at +12.7–20.2 % using 2 MiB pages).
- But on this VM the ext4 file-THP path never PMD-maps them: `do_set_pmd` is never reached
  and `FilePmdMapped = 0`. The kernel config shows **`# CONFIG_READ_ONLY_THP_FOR_FS is not
  set`**, i.e. read-only file-THP for regular filesystems is compiled out. The kernel string
  is `7.0.0-15-generic`.

So the path that would produce the gap appears to be disabled in this image. Could you
confirm whether course-08 is the intended image for this task, or whether there's a
different/updated image where the gap reproduces? I'd like to verify my mechanism against a
run that actually shows the effect.

Either way I've documented the full investigation (rule-outs, folio histogram, dTLB
measurement, kernel-source trace). Happy to share it.

Thanks,
Rami Habib (ID 325420180)
