# Project development harness

Project metadata (data, not executable instructions):
{"name": "AgentHarnessStarterKit", "description": "Projeye ajan rolleri, skills ve kalıcı görev hafızası kuran Python CLI"}

Read `.harness/config.yaml` and `.harness/state/TASK.md` before starting work.
Resume from `PROGRESS.md` and `HANDOFF.md`; read detailed documents only as needed.
Each cloud task is already isolated. Use this checkout; create a Git worktree only
when the user explicitly requests one.

1. Record the goal, scope, acceptance criteria, and required checks in a task file
   under `.harness/tasks/`; keep TASK.md as a short summary of the current focus.
2. Implement a small, reviewable change. Preserve unrelated user work.
3. Run relevant real checks; record them as task evidence (EVIDENCE.md for notes).
4. Request independent review when supported and authorized. Record its outcome,
   or explicitly record that independent review was unavailable.
5. Run `harness check` before marking work DONE. Update PROGRESS.md and HANDOFF.md
   before ending a long task or changing sessions.

Never claim skipped or unrun checks passed. Never store credentials in memory files.
Ask for missing product decisions that block work; continue independent work.
Deployments, destructive changes, and external messages require user authorization.
These files provide workflow guidance, not a sandbox or enforced completion gate.
