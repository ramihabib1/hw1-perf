#!/usr/bin/env python3
"""Assemble a self-contained writeup/SUBMISSION.md:
   cover + Task 1 + Task 2 analysis, then an Appendix embedding every command
   (runbooks) and every raw log/output file — so the single PDF contains everything
   the assignment asks for ("logs of all commands executed and their outputs").
Run: python3 scripts/assemble_submission.py  (then build_submission_pdf.py)
"""
import os, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = os.path.join(ROOT, "writeup")
OUT = os.path.join(W, "SUBMISSION.md")

def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
        return f.read()

def fenced(path):
    """Embed a file's literal content in a ~~~~ fence (safe around inner ``` blocks)."""
    body = read(path).rstrip("\n")
    return f"### `{path}`\n\n~~~~text\n{body}\n~~~~\n"

parts = [read("writeup/00-cover.md"), "\n---\n", read("writeup/task1.md"),
         "\n---\n", read("writeup/task2.md")]

# Appendix A — the commands (runbooks document every command, per phase)
parts.append("\n---\n\n# Appendix A — Commands Executed (Runbooks)\n\n"
             "Every command run in this investigation, organised by phase. Outputs are in "
             "Appendix B.\n")
for rb in ["task1/RUNBOOK.md", "task2/RUNBOOK.md"]:
    parts.append(f"\n## `{rb}`\n\n~~~~markdown\n{read(rb).rstrip()}\n~~~~\n")

# Appendix B — every raw log / tool output
parts.append("\n---\n\n# Appendix B — Raw Logs and Tool Outputs\n")

def collect(globs):
    files = []
    for g in globs:
        files += sorted(glob.glob(os.path.join(ROOT, g)))
    out = []
    for f in files:
        rel = os.path.relpath(f, ROOT)
        if os.path.getsize(f) == 0:        # skip empty (e.g. stderr-only baseline files)
            continue
        out.append(rel)
    return out

parts.append("\n## Task 1 — raw output\n")
for rel in collect(["logs/task1_*.log", "task1/*.txt"]):
    parts.append("\n" + fenced(rel))

parts.append("\n## Task 2 — raw output\n")
for rel in collect(["logs/task2_*.log", "task2/*.txt"]):
    parts.append("\n" + fenced(rel))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("".join(parts))
print("wrote", OUT, "(", sum(p.count(chr(10)) for p in parts), "lines )")
