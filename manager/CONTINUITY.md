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
continuation of that checkpoint; `running` is not a safe interruption point.
Use `waiting`, `awaiting_approval`, or `awaiting_user_input` when continuation is
not currently authorized or useful. Use `completed`, `canceled`, or `superseded`
to prevent further resumption. Never infer a foreground job from old idle history.

The gate rechecks the checkpoint before dispatch and persists receipts before
sending. A duplicate checkpoint or six continuations on one authorization pauses
resumption. Only a genuine new user authorization can reset that budget; do not
rename a checkpoint to bypass a pause. Explain a pause once and ask for direction.

Every round must preserve the foreground record. After a round, reconcile newer
user instructions before resuming ready work. A cancellation, replacement, or
approval wait takes precedence over scheduled text. When a guard has paused
continuation, do not bypass it by resuming during rounds. Keep the round bounded.

Only the local scheduler may request the bounded next turn. The manager must not
message itself, create another monitor, or alter task settings. Uncertain sends
stop dispatch and require inspection. On restart, keep receipts; never restore a
stale ledger over completed work. Missing/malformed ledgers fail closed.

An idle night still generates no AI wakes. Authorized ready work may continue
overnight and consume tokens, within the six-continuation budget.
