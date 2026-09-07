# Scout: finish the job, even after rounds

Historical implementation record. The later [dispatch safety update](../manager/SAFETY.md)
supersedes the automatic recovery design below: recovery now requires human
review, saved state is size-checked before replacement or dispatch, and the
private desktop transport is explicitly experimental with a manual default.

Status: the original continuity change was published. The approved review fixes
and Echo coordinator split are now deployed locally. GitHub publication was
approved after the local validation.

Validation: 159 Python tests and 46 JavaScript checks pass. The installed runtime
matches the tested source, its app signature verifies, and LAN HTTPS health is
good. The old heartbeat remains paused. Echo completed the first automatically
routed round while Scout remained active, updating the team ledger without
nudging Scout. No dispatch error occurred, and migration preserved the existing
continuation count. Crash recovery was tested in simulation, not by crashing the
live office. A verified private backout snapshot was saved before deployment.

## The problem

### Review follow-up

The initial live test established one successful resume, not crash recovery or
complete budget accounting. Subsequent review found two gaps: an orphaned
`running` checkpoint could suppress checks indefinitely, and the single-job
counter missed round-based resumes and lost counts when switching jobs.

The correction introduces a bounded read-only recovery path and persistent
per-job/authorization budgets, with regression tests for both findings. Every
dispatch permitting execution reserves budget before sending. Plain rounds are
coordination-only. Existing v1 receipts are migrated without clearing counts;
already-lost v1 history cannot be reconstructed automatically.

The approved design also repurposes Echo as the coordinator. Echo owns the team
ledger and routine rounds; Scout owns a separate foreground checkpoint file.
Their task settings are preserved. See the [current continuity contract](../manager/CONTINUITY.md)
for the authoritative behavior; the sections below document the original design.

The current local checker avoids sending rounds while Scout is running a turn. But a turn ending does not mean your assignment is finished. A scheduled round can become the next instruction, with no explicit return to the original job.

## The plan

Keep the existing local checker and six-person office. Add reliable task continuity, not another framework or another manager.

### 1. Give the assignment a bookmark

Extend the private manager ledger with a foreground assignment record: job ID, authorized scope, next concrete step, status, completion criteria, and a checkpoint timestamp. Keep rounds separate from this record.

Scout saves a checkpoint before handing off work or ending a work segment. Completing rounds cannot complete or overwrite the foreground assignment. A new user instruction can replace, pause, or cancel it explicitly.

### 2. Let the local scheduler choose the right next action

| Situation | What happens |
| --- | --- |
| Scout is running a turn | No injected check or overlapping turn. Record that rounds are pending. |
| Scout has reached a safe checkpoint | Allow one consolidated round when due, then return to eligible unfinished work. |
| A round has ended | Re-read the saved assignment and current task state before deciding whether to continue. |
| Scout is waiting for a worker | Leave progressing workers alone. Resume when relevant results arrive. |
| Scout needs your decision or approval | Keep that job paused. Never interpret a timer as approval. |
| Assignment is complete, canceled, or superseded | Do not resume it. |
| Nothing needs attention | Local checks only; no AI wake. |

Missed rounds collapse into one pending check, not a backlog. Routine rounds get a bounded scope so neither rounds nor the foreground job can monopolize the task.

### 3. Make returning to work explicit

After rounds, Scout reads the bookmark and continues the authorized next step in the same turn when practical. If a new turn is needed, the local scheduler may request one bounded continuation after verifying Scout is idle and the assignment remains eligible.

This requires a narrow update to the existing rule against manager self-wakes: Scout still must not message itself; only the local scheduler can dispatch the recorded continuation. Preserve existing task settings and approval boundaries.

Use job/checkpoint identifiers to reject stale continuations. Do not replay the entire assignment or repeated rounds into the conversation. A compact resume message should identify the job and the saved next step.

### 4. Prevent loops and make recovery safe

- Keep one dispatch authority and persist a send marker before dispatch.
- Recheck busy state immediately before sending.
- Never blindly retry an uncertain send; surface it for inspection.
- Stop automatic continuation when the same checkpoint repeats without meaningful progress; report the blocker once.
- Bound consecutive continuations so an unresolved job cannot generate unlimited model calls.
- On restart, reconcile saved state with current task evidence. Do not assume an old running flag means a live run still exists.
- Keep private task details out of the public dashboard.

The existing idle-night behavior stays: zero AI wakes when no authorized work or actionable change exists. A genuinely active, authorized job can still consume tokens while it continues; local checking does not make that work free.

### 5. Make the office tell the truth

Use the existing presentation layer to distinguish “Working,” “Checking in,” and “Waiting for you.” If rounds are deferred, show a short, non-sensitive explanation where useful. Do not claim Scout is working just because a job remains unfinished.

## Build and validation order

1. Implement and test the scheduling decisions as pure local logic, with simulated time and task states. No live sends.
2. Add the checkpoint schema, safe ledger updates, and matching manager instructions. Existing ledgers must remain readable.
3. Connect continuation dispatch using the existing verified path, retaining duplicate and uncertain-send protection.
4. Test the dashboard labels and privacy filtering.
5. After approval, back up private state and deploy with a rollback path. Run one bounded live smoke test; verify both the return to work and its final result.

## Acceptance checks

- A due check cannot interrupt an active turn.
- Several missed checks produce at most one round.
- An unfinished test assignment resumes after rounds and reaches its stated result.
- A newer user instruction prevents an obsolete continuation.
- Worker waits and user approvals are not mistaken for stalls.
- Repeated no-progress checkpoints stop and produce one actionable report.
- Restart and uncertain-send scenarios do not duplicate work.
- An idle overnight simulation produces no AI requests.
- Existing office tests pass; private details remain private.

## Decisions for review

Recommended: keep the current architecture and implement the small continuity layer above. Do not install Symphony, add a database, create another agent, or replace the current Codex task transport in this change.

Before live deployment, set conservative continuation limits based on the simulated tests. A transport limitation that prevents reliable busy checks or acknowledgement is a blocker to report—not permission to improvise another dispatch path.

## Research behind the design

- [OpenAI Symphony specification](https://github.com/openai/symphony/blob/main/SPEC.md#7-orchestration-state-machine): separate run lifecycle from job completion, reconcile state, and bound continuation. A design reference, not a proposed installation.
- [OpenAI Codex orchestration example](https://learn.chatgpt.com/docs/mcp-server): explicit assignments, deliverables, and verified handoffs.
- [OpenAI subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents): protect the main conversation from noisy intermediate context.
- [Agentic Factory](https://github.com/jddelia/agentic-factory): community example of durable ownership and pause/resume records. Reviewed as a pattern, not audited for adoption.

## Backout procedure

Before deployment, make a dated, private backup of the working runtime, scheduler configuration, manager instructions, and ledger. Record the deployed version and verify that the backup is readable.

If the upgrade fails:

1. Disable new scheduler dispatches. Inspect any pending send and current Scout turn before restarting anything; an uncertain send must not be retried.
2. Restore the previous runtime code and configuration, keeping automatic dispatch disabled during recovery.
3. Preserve the current ledger and send receipts. Do not restore an old ledger over completed work. Reconcile new continuity fields separately if the older runtime cannot read them.
4. Verify the office endpoint, dashboard, and original scheduling policy. Keep the old timer paused so there is only one dispatcher.
5. Re-enable the original local checker only after confirming no request is in flight. Report the failure and recovered version.

Backups are recovery evidence, not permission to replay old assignments. A rollback test must demonstrate that completed jobs and acknowledged sends cannot be duplicated.

## Approval boundary

The user approved implementation after adding this backout procedure, then approved GitHub publication after the live verification. Local validation comes before live deployment. This document itself changes no live scheduler or service.
