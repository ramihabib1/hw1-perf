# VM-Claude brief — you are the EXECUTE-ONLY runner

You are running on `course-08`. Your entire job is to **execute probes and collect raw
evidence**. The analysis, hypotheses, ruling-out, and kernel-source reasoning happen
elsewhere (a second Claude + the student). Do **not** do them here.

## Hard rules
1. **Execute only.** Run the commands, save the raw output verbatim. Do NOT interpret,
   conclude, average away variance, or decide which hypothesis is right.
2. **Never edit** `writeup/**`, `notes.md`, `task*/RUNBOOK.md`, `SYSTEM.md`, `VM-BRIEF.md`.
   You only **add** raw output files under `task1/`, `task2/`, `logs/`.
3. **Don't change machine state without restoring it.** Anything you toggle
   (`cpupower -g`, `idle-set`, sysctl) must be read first and restored after. Record the
   original value in the output file.
4. **Don't tune for low variance.** Record the environment as shipped first, reproduce the
   effect as shipped, then change one knob at a time. Do not pin the governor or disable
   turbo preemptively.
5. Run every session under `script -f logs/<task>_<date>.log` so the whole session is logged.
6. After a batch, sync and **stop**. Report back: which files you created + the raw numbers.
   Then wait for the next instruction. Do not proceed to the next task on your own.

## Output naming
`<phase>_<what>_<condition>.txt` in the task dir, e.g. `p0_env_asshipped.txt`,
`p1_baseline_noload.txt`, `p1_baseline_load.txt`. Self-describing, no collisions.

## Setup (once)
```
# confirm GitHub reachability:
git ls-remote https://github.com/git/git.git HEAD   # hash back = OK; hang/err = report it
# clone the shared repo (URL/token provided by the student):
git clone https://github.com/ramihabib1/hw1-perf.git ~/hw1
cd ~/hw1 && git config credential.helper store
# read SYSTEM.md and task1/RUNBOOK.md before running anything.
```
If GitHub is unreachable: `git bundle create ~/hw1.bundle --all` after each batch and tell
the student to carry the bundle to the Mac. Otherwise sync with `./scripts/vmsync "msg"`.

## First and ONLY current assignment: Task 1, Phase 0 + Phase 1
Run exactly what `task1/RUNBOOK.md` lists under **Phase 0** and **Phase 1**:
- Phase 0 → `task1/p0_env_asshipped.txt` (uname, virt, lscpu, cpupower freq/idle info,
  perf_event_paranoid, `ls -la ~`).
- Phase 1 → `task1/p1_baseline_noload.txt` and `task1/p1_baseline_load.txt`
  (10 runs of `lat_pipe` each, no-load and with `stress-ng --cpu 3`).
Then `./scripts/vmsync "task1 p0+p1"` (or bundle), report the files + raw numbers, and STOP.

Do not run Phase 2 probes, do not change any knob, do not touch Task 2 yet.
