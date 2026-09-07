# Optional office manager

Choose one of your existing pinned Codex tasks as the manager. The example office calls that task **Echo (Coordinator)**; you can use any name.

## Prepare local configuration

Pin the manager and its teammates, then run this from the repository root:

```sh
python3 manager/configure.py --manager-key 2
```

This reads the current local pins and creates two private, ignored files:

- `manager/team.json`: stable task IDs and the manager assignment.
- `manager/MANAGER-BRIEF.md`: the coordination brief with this checkout's local path.

It does not start work or install an automation. Existing configuration is preserved. To choose a different pin, change `--manager-key`; to use a different Codex directory, pass `--codex-dir PATH`.

## Authorize the manager

Open the chosen manager task and ask it to read the generated brief. For example:

> Read manager/MANAGER-BRIEF.md in this repository and act as office manager for the roster in manager/team.json. Coordinate routine next steps on jobs I have already authorized. Report blockers and decisions to me. Run an initial check, leave actively progressing work alone, and establish a baseline for old idle history. Then use Codex's automation tool to create a heartbeat in this task every ten minutes. Stay quiet when nothing meaningful changes, including when there are no active jobs. Do not create a duplicate monitor.

Give the task the full local path if the repository is outside its current working folder. Review the initial check before relying on the recurring workflow. The manager maintains a private `state.json` ledger to avoid repeated nudges and reports.

The schedule runs inside your existing manager task. It may wake while the team is idle to discover new assignments; idle checks should stay quiet. Local checks need the Mac and Codex running. The repository does not contain an installed schedule, and cloning it does not change any of your automations.

The whiteboard is a view of `manager/state.json`, not an automatic summary of every Codex or sub-agent action. The manager updates that ledger after a real roster check and after work it coordinates in its own task. A heartbeat may not run while the manager task is already busy, so the active manager run must refresh the ledger before it finishes.

For an immediate reconciliation, say this in the manager task:

> Refresh the office board now. Inspect the roster and any work you coordinated in this task, reconcile the private ledger without replacing unrelated history, resolve only decisions the user has actually answered, and update its freshness timestamp. Tell me only about meaningful changes or blockers.

`lastCheckAt` means the ledger was actually reconciled; it should never be advanced merely to hide the stale badge. Each changed job also gets its own `updatedAt` timestamp.

The live whiteboard and result panels remain generic until the manager writes explicitly safe `presentation` labels and summaries into the private ledger. The generated brief explains this boundary and the supported work stages. Edit `manager/team.json` to add a safe `presentation.label` for each agent if you want names on the board; raw task titles, objectives, evidence, and local paths are never copied automatically.

## What the manager does

- Reads current progress using native task tools.
- Tracks actual user-authorized objectives rather than inferring work from task names.
- Advances a clear, routine next step when a tracked job needs it.
- Leaves actively progressing tasks alone and avoids repeating an unchanged follow-up.
- Reports meaningful completions, actionable failures, blockers, and decisions in its own task.
- Preserves the original task's scope and approval boundaries.

Ask **"Give me the office report"** or **"What's waiting on me?"** for an immediate update.

## Missing message context

Some desktop versions return task status while omitting current message text. The bounded fallback reads recent public messages from the configured tasks' local session files:

```sh
python3 manager/read_task.py TASK_ID --turns 2
```

The output is private: it may contain task requests, progress, names, and local paths. It distinguishes delegated input from direct user messages, marks truncation, and excludes reasoning and arbitrary tool output. Missing or truncated text does not establish authorization. The helper never sends messages and is not available through the office web server.

Keep generated configuration and history out of commits. The repository's `.gitignore` excludes them by default.

## Local gate: no AI calls while the office is idle

Optional foreground continuity is documented in [CONTINUITY.md](CONTINUITY.md).
Enable it deliberately with `continuityEnabled: true` after backing up runtime
and private state. It adds bounded resumption of explicit checkpoints, not an
idle AI timer. Do not restore old job ledgers during rollback.

You can also use a separate existing task for lightweight rounds (for example,
Echo) while the project lead (Avina, CoS) keeps focused work. The optional
`foregroundThreadId` setting routes recovery and continuations to that lead;
rounds stay with the coordinator. Each has a separate private state file to
avoid concurrent ledger edits. See [the split-mode contract](CONTINUITY.md#separate-coordinator-optional)
and [the coordinator brief](COORDINATOR.md). No extra agent or AI timer is needed.

The time-based heartbeat above is the simpler option, but it starts an AI turn
even when there is nothing to do. The opt-in local gate replaces that timer with
a Python status check every 15 seconds. No model, prompt, conversation text, or
network service is needed to decide that the team is idle.

- A new worker lifecycle/status change settles for 30 seconds before a round.
- Active workers get at most one manager wake every ten minutes. Changes during
  that cooldown are retained for the next round.
- Completed work is checked once. Unchanged idle or approval-wait states do not
  create further rounds. Brief jobs that finish between polls are still detected
  through their completion timestamps.
- The coordinator's own activity never triggers another coordinator run. A busy or waiting
  manager is not interrupted; pending worker changes wait until it is idle.
- Startup takes a baseline of old idle history. Missing or uncertain live status
  does not count as active work.

When the desktop's optional status broadcast is unavailable, the gate uses
explicit local start/completion/interruption events, not silence or file age, to
identify work and a free manager. Old unfinished activity becomes unknown and
cannot trigger a wake. Without runtime approval flags, an approval wait cannot
be distinguished immediately from work; the uncertain fallback expires instead
of continuing overnight rounds. The manager's desktop owner also enforces its
normal no-overlapping-turn checks when a wake is submitted.

Ask your manager to configure this mode explicitly. It must create an owner-only
`manager-gate.json` file in the desktop app's private Application Support folder,
with `enabled: true`, `managerThreadId` matching the private roster, and a `prompt`
containing your existing bounded manager instructions. Never put private IDs or
the resulting gate state in a public commit. The native launcher recognizes this
file on its next server start. For a manually managed server, pass
`--manager-gate-config /absolute/path/to/manager-gate.json` instead.

After testing the gate, **pause the old heartbeat using the app's automation
tool**. Do not leave both dispatchers enabled. The gate does not create, edit, or
resume any app automations. Cloning the repository never enables it.

The office server must remain running on an awake Mac, and ChatGPT/Codex must be
running with the manager task available. Closing the office browser is fine.
The desktop launcher keeps the local server running when its window closes.
Use the existing optional Start at Login feature if you want it after login.

### Safety and recovery

This is an observed internal desktop protocol, checked against the September
2026 app. It is not a public OpenAI scheduling API. The gate discovers the
existing manager task owner and requests one turn there, inheriting its current
model, workspace, and permissions. It does not launch a separate CLI agent,
approve anything, or change task settings. HTTP remains read-only.

Private `manager-gate-state.json` records the metadata baseline, local check time,
wake count, and pending send marker. A file lock prevents duplicate gate
processes. The pending marker is persisted before a send: a crash, timeout, or
unknown response leaves dispatch paused. It never blindly retries a send.
Successful acknowledgement is checked against the IPC envelope's request ID,
method, success status, and discovered owner. The result does not repeat the
method. Errors retain only a phase and exception type, not private response text.

If the footer says the watch needs attention, inspect the manager task to see
whether a turn arrived before rearming. Preserve the state file for diagnosis;
do not simply delete it and retry. Correct the problem, clear only a verified
failed/finished dispatch marker and error, and restart the local server. To
disable the watch, set `enabled` to `false`; it stops at its next local poll.
The paused heartbeat remains available as a manual rollback, but disable the
local gate before enabling it again.

If the request actually arrived, record it as handled (advance `wakeCount` and
`lastWakeAt`, clear the consumed `pendingAt`, and mark installation verification
complete when applicable). Keep evidence of the matching received turn in the
private ledger. Do not reset the whole state: that could replay an already
completed check. Clear associated `errorPhase`/`errorKind` only after review.

For installation QA only, `verifyFirstWake: true` requests one smoke-test round
after the manager becomes idle. Its completion marker is durable, so it cannot
become an idle timer. Remove the option once the actual incoming manager turn
has been verified. This one deliberate test uses AI tokens.

Tests: `python3 -m unittest -v test_manager_gate.py` from the repository root.
They include a simulated 12-hour idle night, quick completions, busy manager,
waiting-only states, reconnect uncertainty, cooldown, private storage, and
duplicate-send protection. Only actual manager and worker turns use AI tokens;
this does not eliminate usage from other scheduled tasks on your account.
