#!/usr/bin/env python3
"""Assemble writeup/SUBMISSION.md: cover + Task 1 + Task 2 + a SHORT appendix of the key
raw logs that back the conclusions. Run: python3 scripts/assemble_submission.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "writeup", "SUBMISSION.md")

def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
        return f.read()

# Only the logs actually referenced by the analysis — keeps the document short.
TASK1_LOGS = [
    ("logs/task1_20260610_1427.log", "baseline: lat_pipe x10, no-load then with-load"),
    ("task1/p2_perfstat_noload.txt", "perf stat, no load (frequency check)"),
    ("task1/p2_perfstat_load.txt",   "perf stat, with load"),
    ("task1/p2_pin1core_noload.txt", "both pipe ends pinned to one core"),
    ("task1/p2b_pin2core_idle.txt",  "two cores, allowed to idle"),
    ("task1/p2b_pin2core_busy.txt",  "two cores, kept busy"),
    ("task1/p4_offcpu_hist_noload.txt", "off-CPU latency histogram, no load"),
    ("task1/p4_offcpu_hist_load.txt",   "off-CPU latency histogram, with load"),
    ("task1/p4_callgraph_noload.txt",   "perf sched callgraph, no load"),
]
TASK2_LOGS = [
    ("task2/p10_reboot_confirmed.txt", "the gap, with perf stat (dTLB collapse) and smaps (FilePmdMapped=64MiB for v2)"),
    ("task2/p5_folio_hist_v1.txt", "folio-order histogram, v1"),
    ("task2/p5_folio_hist_v2.txt", "folio-order histogram, v2"),
    ("task2/p1b_filefrag.txt",     "filefrag (fragmentation is trivial)"),
    ("task2/p2_perfstat_v1.txt",   "perf stat v1 (page-faults are all minor -> fully cached)"),
]

def appendix(title, items):
    out = [f"\n## {title}\n"]
    for path, desc in items:
        if not os.path.exists(os.path.join(ROOT, path)):
            continue
        body = read(path).rstrip("\n")
        out.append(f"\n### {path}\n*{desc}*\n\n~~~~text\n{body}\n~~~~\n")
    return "".join(out)

parts = [
    read("writeup/00-cover.md"), "\n---\n",
    read("writeup/task1.md"), "\n---\n",
    read("writeup/task2.md"), "\n---\n",
    "\n# Appendix: raw command outputs\n",
    appendix("Task 1", TASK1_LOGS),
    appendix("Task 2", TASK2_LOGS),
]

with open(OUT, "w", encoding="utf-8") as f:
    f.write("".join(parts))
print("wrote", OUT)
