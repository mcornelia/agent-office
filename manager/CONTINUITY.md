# Foreground continuity contract

Opt-in with `continuityEnabled: true` in the private gate configuration. No new
timer is installed. Existing ledgers without a foreground record retain their
original behavior. The ledger is private and must never be published.

Use a top-level `foreground` object in `state.json` for explicitly authorized
manager work only:

```json
{
  "jobId": "stable-job-id",
  "authorization": "original-user-turn-or-timestamp",
  "checkpoint": "meaningful-progress-identifier",
  "nextStep": "One concrete authorized action",
  "status": "ready"
}
```

Keep objective, acceptance criteria, and evidence in the corresponding job row.
Save actual progress before ending a work segment. `ready` grants one bounded
continuation of that checkpoint; `running` is not permission to resume. If the
task is positively idle while its checkpoint still says `running`, the gate
shows **Recovery needed** and can request one read-only recovery check per job
and authorization. It never infers a crash from elapsed time alone. Recovery
reconciles current evidence and records a safe next step or a blocker; it cannot
execute work or repeat an uncertain external action. Unresolved recovery does
not suppress the team's routine rounds. Use `recovery_needed` when evidence
does not establish a safe next step.
Use `waiting`, `awaiting_approval`, or `awaiting_user_input` when continuation is
not currently authorized or useful. Use `completed`, `canceled`, or `superseded`
to prevent further resumption. Never infer a foreground job from old idle history.

The gate rechecks the checkpoint before dispatch and persists receipts before
sending. A duplicate checkpoint or six continuations on one job/authorization pauses
resumption. Only a genuine new user authorization can reset that budget; do not
rename a checkpoint to bypass a pause. Explain a pause once and ask for direction.

Budgets are persistent entries keyed by job and authorization, not a single
current-job counter. A -> B -> A retains A's count and all reserved checkpoints.
Both separate continuations and combined round-plus-resume turns reserve one
unit before the send; a failed/uncertain send retains that reservation. A plain
coordination-only round cannot resume work. Each reservation permits only one
bounded step for its exact job/authorization/checkpoint. Re-read these identifiers
before acting; a different current assignment makes the request stale.

The v2 state format imports the surviving v1 count and last checkpoint once.
History already overwritten by v1 cannot be reconstructed automatically. Legacy
top-level count fields remain backup evidence, not the active accounting source.
Do not delete old per-job entries to regain budget. If state exceeds its private
file size limit, dispatch fails closed and requires operator review.

Every round must preserve the foreground record. Only an explicitly reserved
combined round may resume work afterward. A cancellation, replacement, or
approval wait takes precedence over scheduled text. Keep the round bounded.

## Separate coordinator (optional)

Set the roster's `managerThreadId` and the gate's matching `managerThreadId` to
the existing coordinator task. Set the gate's `foregroundThreadId` to the
existing task doing substantive work. Both must be in the same local roster.
Keep existing model, reasoning, and approval settings; lightweight means bounded
coordination work, not an implicit model downgrade.

In this mode the coordinator owns `state.json` and reads but never edits
`foreground.json`. The foreground task alone writes `foreground.json`, containing
the same top-level `foreground` object shown above plus its private job evidence.
Do not keep a second active foreground checkpoint inside `state.json`. The
coordinator reads task evidence and updates the team ledger; the foreground task
must not concurrently write it. Follow [COORDINATOR.md](COORDINATOR.md) for rounds.

Routine rounds target only the coordinator, even while the foreground task is
busy. They cannot resume its work or consume its continuation budget. Continuation
and recovery requests target only the foreground task, with a fresh busy-state
check. The coordinator's own activity is excluded from round triggers. The
existing office communication animation follows the coordinator's real checks.

Only the local scheduler may request the bounded next turn. The manager must not
message itself, create another monitor, or alter task settings. Uncertain sends
stop dispatch and require inspection. On restart, keep receipts; never restore a
stale ledger over completed work. Missing/malformed ledgers fail closed.

For rollback to the older gate, disable foreground continuity before restoring
old code. Keep the v2 receipts and both current ledger files; never replace them
with stale backup state. Restore the previous coordinator/roster configuration
deliberately, keep the old heartbeat paused, and verify only one dispatcher runs.

An idle night still generates no AI wakes. Authorized ready work may continue
overnight and consume tokens, within the six-continuation budget.
