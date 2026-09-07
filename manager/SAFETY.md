# Dispatch safety

The office can observe work without starting work. Keep those capabilities
separate, especially on a work-managed Mac.

## Two explicit modes

- `manual` (the default): local activity is observed, but the gate never opens
  a dispatch connection, reserves execution, or sends a prompt. Ask the
  coordinator for a check-in yourself, using the app's normal tools.
- `desktop-experimental`: opt in to the existing, observed private desktop
  protocol. It inherits the receiving task's normal settings and permissions.
  Unknown modes, unavailable connections, and uncertain sends stop dispatch.
  There is no silent switch to another transport.

`manager_gate.py` owns policy, receipts, and pre-send checks.
`desktop_dispatch.py` owns the private connection. Changing transport must not
bypass the gate's authorization, freshness, budget, or duplicate-send checks.

## Recovery is human-reviewed

A saved job marked running while its task is positively idle shows **Recovery
needs review**. No recovery turn is automatically dispatched. The user asks the
lead to reconcile current instructions, results, and receipts before deciding
whether it is safe to resume. The lead preserves uncertain effects and approval
waits; the coordinator never grants permission on the user's behalf.

A future automated recovery context would need enforced filesystem, network,
tool, and connector restrictions—not just a read-only prompt. It should return
findings for a controlled checkpoint update, not inherit a normal working
task's full capabilities. Until that boundary is verified, recovery stays manual.

## Documented integration options

OpenAI documents [App Server](https://learn.chatgpt.com/docs/app-server) for
building custom clients, and the [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
for programmatic jobs. These are candidates to evaluate, not drop-in replacements
for controlling existing desktop tasks. We have not verified equivalent access
to the same pinned tasks, settings, approvals, or desktop-owned sessions.

See [OpenAI's permissions documentation](https://learn.chatgpt.com/docs/agent-approvals-security)
for actual enforcement controls. On a work-managed Mac, follow the organization's
policy and use manual/native operation unless an integration is approved.

## State and rollback

The full serialized state must fit within 128 KiB before a save or send. On
overflow, stop and retain the last valid file. Do not delete receipts or reset
budgets to create space. If a send occurred but saving its result fails, retain
the pre-send marker and inspect the destination before any retry.

Before a local upgrade, back up runtime code, configuration, and receipt evidence.
Stop only the verified owned server, then install the tested runtime. Preserve
current ledgers, receipt files, budgets, task settings, and the paused old timer.

To back out, stop the owned server and restore code/configuration only. Keep
the gate disabled (`enabled: false`) when restoring older code that lacks these
guards; `manual` is not understood by that old version. Start the read-only
viewer, verify health, and request a deliberate re-enable decision. Never restore
an old ledger or send-receipt snapshot over current state, or run both schedulers.
