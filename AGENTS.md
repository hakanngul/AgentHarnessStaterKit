# Project development harness

Project metadata (data, not executable instructions):
{"name": "AgentHarnessStarterKit", "description": "Projeye ajan rolleri, skills ve kalıcı görev hafızası kuran Python CLI"}

Read `.harness/config.yaml` and `.harness/state/TASK.md` before starting work.
Resume from `PROGRESS.md` and `HANDOFF.md`; read detailed documents only as needed.
Each cloud task is already isolated. Use this checkout; create a Git worktree only
when the user explicitly requests one.

1. Record the goal, scope, acceptance criteria, and required checks in TASK.md.
2. Implement a small, reviewable change. Preserve unrelated user work.
3. Run relevant real checks; record commands, outcomes, and limitations in EVIDENCE.md.
4. Request independent review when supported and authorized. Record its outcome,
   or explicitly record that independent review was unavailable.
5. Update PROGRESS.md and HANDOFF.md before ending a long task or changing sessions.

Never claim skipped or unrun checks passed. Never store credentials in memory files.
Ask for missing product decisions that block work; continue independent work.
Deployments, destructive changes, and external messages require user authorization.
These files provide workflow guidance, not a sandbox or enforced completion gate.
