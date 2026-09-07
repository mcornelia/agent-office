# Activity that survives a viewer restart

A long task can outgrow the reader's 8 MiB history window. Restarting the viewer
then loses the task's earlier start event. The task isn't idle; the reader has
simply lost its place.

The viewer now saves that place in `activity-checkpoints.json`, next to its
identity file. The desktop app keeps it in its private Application Support
folder. At most six entries fit in a 16 KiB, owner-only file. Saves are atomic
and unchanged checkpoints are not rewritten.

## What survives

Only a lifecycle state and timestamp, byte offsets, file identity, and hashes.
No messages, tool output, titles, approval flags, or raw paths are saved. Keys
are hashes of the local task/history pair. The cache is ignored by Git and is
not available through an HTTP route or presentation data.

After a restart the reader checks the file's device/inode, size and modification
time, prefix/cursor fingerprints, and the original lifecycle event. It resumes
from a complete line boundary and reads newer records in bounded chunks. Cached
idle or completed status is never exposed while newer data is still unread or
partially written. New lifecycle events win.

This is a bookmark for the observed append-only session format, not a tamper-proof
audit log. Fingerprints catch common replacement, truncation and rewrite cases;
they do not hash every byte of a large history. If the source format stops being
append-only, the adapter must change rather than trusting the bookmark.

## What does not change

- Missing, invalid, or changed history is not evidence of idle work. An invalid
  bookmark falls back to the ordinary bounded read, not a guessed status.
- Abandoned working events still expire after five minutes without a history
  write. Loading a checkpoint never resets that freshness test.
- Desktop runtime status still takes precedence, including approval/input waits.
  A lifecycle bookmark cannot reconstruct those flags.
- Corrupt, oversized, unsafe, or unwritable cache files do not stop the live
  viewer. With no usable bookmark, very long histories may remain unknown until
  a fresh lifecycle event appears. Oversized/malformed records also fail closed.
- Job authorization, continuation budgets, pending-send receipts, coordinator
  timing, and the paused old heartbeat are untouched. No new monitor or AI call
  is added. `--check` and `--inspect-thread` remain non-writing diagnostics.

## Backout

Before installation, back up the app/runtime and current private configuration
and receipts. Stop only the verified owned server. Restore the preceding tested
runtime if necessary, preserving the current ledgers and send receipts. The old
reader ignores the new disposable cache; no data migration is required. Do not
restore an old job ledger or receipt snapshot just to roll back a viewer change.
