# Agent Office

**A live office for your Codex agents.**

Six pinned tasks become six characters with their own desks, nameplates, and activity colors. A live whiteboard shows each manager-tracked assignment and its explicit stage, the **Needs you** tray holds decisions until they are resolved, and the results shelf keeps completed work handy. Working agents walk between their desks, the whiteboard, and the coffee machine—because no coffee means no workee. When a task needs your input or approval, its monitor, illustrated key, and character badge turn amber.

Agent Office is a local companion for the Codex desktop app on macOS. It pairs with the Creator Micro 2's built-in **Pinned chats** mapping and also works while you select tasks normally in Codex.

## Start the office

You need the Codex desktop app on the same Mac, Python 3.9 or newer, and a modern browser. The application uses Python's standard library; there are no packages to install.

```sh
git clone https://github.com/mcornelia/agent-office.git
cd agent-office
python3 server.py
```

Open **http://127.0.0.1:4318/** in your browser. Pin up to six local tasks in Codex and start working. The office updates about once a second.

On macOS, you can also double-click **Start Agent Office.command**. Stop the server with Control-C in its terminal.

For a native menu-bar home with a private fullscreen presentation view, see [desktop/README.md](desktop/README.md). The desktop prototype detects and reuses a running server, prevents duplicate launcher starts, and keeps Start at Login opt-in and reversible.

To expose the office securely to devices on a trusted local network through Caddy, see [LAN HTTPS deployment](docs/lan-https.md). The Python backend remains loopback-only; the proxy is the only LAN-facing listener.

For a different local Codex directory or port:

```sh
python3 server.py --codex-dir ~/.codex --port 4319
```

## Meet the team

Each task keeps its character when you reorder the pinned tasks. Rename a task to change its desk plaque. Put a role in parentheses to show it on the second line:

```text
Avina (CoS)
Echo (Coordinator)
Bolt (Product SWE)
Pixel (AI Art Curator)
Atlas (Travel Agent)
Nova (Pick up player)
```

These are example names. The live office uses your current pinned task names. Both the office and illustrated keypad follow the physical arrangement: desks 1 and 2 sit in the upper middle, desks 3–6 form the lower row, and the two upper corners hold the whiteboard and coffee machine. Characters use the open center corridor when moving between those shared spaces instead of cutting through a teammate's desk.

| Color | Meaning |
| --- | --- |
| White | On a break ☕ (confirmed idle) |
| Blue | Working |
| Amber | Waiting for your input or approval |
| Green | Complete, with an unread result |
| Red | Observed error |
| Dark | Reconnecting, status unavailable, or no assigned task |

Movement illustrates activity; a walk to the whiteboard does not mean a particular tool is running. Reduced-motion preferences are respected.

**Meet Scout, the office Golden Retriever.** He wanders the center corridor
while agents work, stops for a sniff, and naps on his bed when it's quiet.
Click or tap him for a tail wag. He's browser-only morale: no agent slot, AI
calls, or messages. Hidden pages pause his stroll; reduced motion keeps him
stationary. Avina (CoS), formerly named Scout, remains the team's lead.

The neon **OPEN** sign lights up when at least one agent is working, including
the manager. It goes dark when everyone is idle, finished, or waiting for input.
If the live feed is interrupted, the dark sign and assigned desks say
**Reconnecting…**, not **On a break**. A connected feed with an unknown task
state still says **Status unavailable**. The sign itself uses no AI.

The page pauses polling while hidden and reconnects when you return, restore it
with Back/Forward, or regain network connectivity. Interrupted requests cannot
overwrite a newer snapshot or start duplicate polling loops. After updating an
already-open office page, reload it once to load the new recovery code.

## Use the Creator Micro 2

1. Enable the keyboard's native Codex layer and **Pinned chats** mapping.
2. Pin your tasks in the order you want for keys 1–6.
3. Keep Codex focused, press an agent's physical key, and give that task work.
4. Watch its character and activity colors update in the office.

The office's on-screen keys—or number keys while the page is focused—select and briefly spotlight a character for inspection. Task selection, prompting, and physical keyboard LEDs remain controlled by Codex.

The included [keyboard signal prototype](docs/keyboard-selection.md) confirms the Creator Micro's six key-to-slot mapping, but the current Codex desktop app does not export a reliable external **selected task changed** event. A raw key press can be consumed by another Codex control without changing tasks, so Agent Office deliberately does not claim a physical-key spotlight yet. Shipping the truthful disabled state prevents the office from highlighting the wrong agent.

## Give the office a manager

The optional manager workflow lets an existing task inspect teammates, coordinate routine next steps on already-authorized jobs, and report meaningful results, blockers, and decisions. The viewer and manager run independently.

See [manager setup](manager/README.md). Installing or running the viewer does not send prompts or create automations. Manager setup requires an explicit instruction in your chosen Codex task.

An optional **local manager gate** checks activity without a model and requests a
manager round only when workers change state or active work needs its ten-minute
check. Idle overnight polling uses zero AI tokens. Actual manager rounds and
worker tasks still use the account's normal allowance. This opt-in desktop
integration uses observed internal IPC, not a supported public wake-up API; it
stops automatic dispatch on an uncertain send instead of risking duplicates.

With the local watch enabled, the manager's desk says **Watching team** between
rounds and shows the last completed ledger check in your browser's local time.
**Checking in** follows real manager communication events; direct project work
still says **Working**. Paused, disabled, or stale watch data is labeled explicitly.
These labels do not change activity colors, OPEN-sign behavior, or check timing.

When the configured manager checks the team, that character carries a clipboard and walks to that teammate's desk before the animated speech bubbles appear. Follow-up messages have their own labels. Real check-ins queue one at a time, skip expired events, and return both characters to normal activity without exposing message text. Reduced-motion mode keeps the same information stationary.

## Local data and compatibility

The server binds only to `127.0.0.1`. It rejects unexpected Host and Origin headers, has no write API, and does not serve arbitrary files. No analytics, external fonts, or cloud service is required by Agent Office.

It reads pinned-task metadata from a local SQLite database in read-only mode and observes local session events. A read-only subscription to Codex's local desktop status stream supplies working, approval-wait, and input-wait states. Only status fields are retained from stream snapshots; conversation text, commands, approval details, and tool output are never sent to the web page.

`identities.json` stores local task-to-character assignments. The manager's roster, brief, and job ledger are also local. These files are excluded from Git. The manager's optional message-reading helper runs separately and is never exposed through HTTP.

**Compatibility:** the local database, session files, and desktop status stream use observed internal formats. Codex updates may require an adapter change. This version was checked with the macOS desktop app in September 2026; it is not a supported public Codex status API. Windows, remote tasks, and ChatGPT cloud tasks are not currently supported.

If the desktop status stream has no record for a task, a fresh local `task_started` event can still show that task as working, but the office does not guess whether it is waiting for approval. That event fallback expires after five minutes; stale or missing activity shows as unavailable. Completed and idle session states can still be displayed. Keep the Mac and Codex running for live status and local manager checks.

## Development

Codex contributors should read [AGENTS.md](AGENTS.md) for working agreements,
validation commands, and review rules. It links to the existing role contracts;
it does not assign a manager role or start monitoring.

For a standing team, [per-member role guides and onboarding](team/README.md)
provide specialties and shared working agreements without creating new tasks
or changing their existing projects and settings.

Codex discovers project instructions from its project root through its working
directory. Start in this repository for direct discovery. If your task starts
in a parent workspace, use a narrowly scoped parent `AGENTS.md` that tells it to
read this repository's handbook only for Agent Office work. Existing coordinator
and foreground briefs also point to the handbook. Verify loaded instructions in
a fresh run; do not assume an already-running task has reloaded them. See the
[official AGENTS.md guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

The interface is plain HTML, CSS, and JavaScript. There is no frontend build dependency.

```sh
# Rebuild index.html from the scene and page shell.
python3 build_view.py

# Run the activity, status, and message-reader checks.
python3 -m unittest -v test_server.py test_desktop_status.py test_communications.py
python3 -m unittest -v test_job_board.py
python3 -m unittest -v desktop/test_agent_office_ctl.py
python3 -m unittest discover -s manager -p 'test_*.py' -v

# Exercise the browser scene and page hide/return/network recovery.
node --test test_rounds.cjs test_polling.cjs

# Inspect your current six slots locally. Output contains private task IDs.
python3 server.py --check
```

| File | Purpose |
| --- | --- |
| `office.html` | Characters, desks, colors, and movement |
| `page-shell.html` | Browser page and local activity polling |
| `build_view.py` | Generates the committed `index.html` |
| `server.py` | Read-only local HTTP server and activity mapping |
| `desktop_status.py` | Local desktop status subscription |
| `communications.py` | Recent manager check and message metadata |
| `job_board.py` | Privacy-filtered manager job, decision, and result projection |
| `manager/` | Optional coordination brief, configuration, and context reader |
| `manager_gate.py` | Opt-in, metadata-only local watcher and bounded manager wake-up |

The standalone `office.html` scene has an example activity preview for interface development. `index.html` always connects to the local activity source.

When reporting a compatibility issue, include your macOS, Python, and Codex versions and the visible symptom. Remove personal task names, IDs, paths, and conversation content from any shared diagnostic output.

## License

Agent Office's original source code and documentation are licensed under the
[MIT License](LICENSE). This does not license Codex, macOS, Creator Micro hardware
or firmware, third-party trademarks, or private data displayed by the office.
