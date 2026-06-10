# Working system — VM ⇄ Mac over git

Two clones of one **private** GitHub repo. The VM produces raw probe output; the Mac
(Claude) does analysis and writeup. They touch **disjoint files**, so syncing never
conflicts.

```
  course-08 (VM)                 GitHub (private hub)              Mac (this repo)
  ~/hw1  ──── vmsync ──push──▶   origin/main   ◀──pull/push──   Claude analyzes
  runs probes, writes               history =                   writeup/notes.md
  raw output files                  the trail
```

## File ownership (the rule that prevents merge conflicts)
| Path                         | Written by | Never touched by |
|------------------------------|------------|------------------|
| `task1/*.txt` `task2/*.txt`  | **VM only**| Mac              |
| `logs/*.log`                 | **VM only**| Mac              |
| `writeup/**`, `notes.md`     | **Mac only** (Claude) | VM    |
| `task*/RUNBOOK.md`, `SYSTEM.md` | **Mac only**        | VM    |

VM adds raw files; Mac reads them and writes the analysis. Disjoint → clean rebases.

## Naming convention for raw output (self-describing, collision-free)
`<phase>_<what>_<condition>.txt` — e.g. `p1_baseline_noload.txt`, `p2_perfstat_load.txt`,
`h2_noidle_noload.txt`. The runbooks already emit these names.

## VM-side loop (after every probe)
```
./scripts/vmsync "task1 phase1 baseline"
```
That pulls, adds raw output, commits (timestamped = the trail), pushes. One command.

## Mac-side loop (Claude, each turn)
`git pull --rebase` → ingest new raw files → update notes.md → commit → push.

## NOT synced (see .gitignore)
- `*.returned.md` — VM password/SSH. **Never** leaves this Mac.
- `*.pdf` — lecture decks, local-only.
- `v1/ v2/ test_file.*` — regenerable sysbench working sets.

## Fallbacks if the VM can't reach github.com
- **Bundle:** on VM `git bundle create ~/hw1.bundle --all`; move that one file to the Mac
  (web console download / scp via jump host); on Mac `git pull ~/hw1.bundle main`.
  Preserves full history, single file.
- **Paste:** small outputs pasted into chat. Always works, doesn't scale.
