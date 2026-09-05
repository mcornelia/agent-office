# Optional office manager

Choose one of your existing pinned Codex tasks as the manager. The example office calls that task **Scout (Mr Manager)**; you can use any name.

## Prepare local configuration

Pin the manager and its teammates, then run this from the repository root:

```sh
python3 manager/configure.py --manager-key 1
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
