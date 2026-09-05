# Physical keyboard selection: feasibility result

**Recommendation: REVISE the production spotlight plan.** Keep selection in the
office page as a page-local choice until a live selection source is verified.
The native app has a useful physical-key event, but the current Agent Office
socket does not provide a verified transport for it. Physical testing is blocked
by the absence of a compatible Micro on this Mac during this investigation.

This is a bounded prototype in `prototypes/keyboard-selection/`, researched on
2026-09-05 against installed desktop app version **26.901.41600**. It changes no
production UI or server and does not install a service. The source inspection,
one live IPC observation, and synthetic tests establish different things; none
of them is a measurement of a physical key press.

## What the native app knows

The installed application contains this path:

```text
Work Louder vendor HID notification: key, act, optional agent
  -> CodexMicroService.handleHidEvent
     resolves AG00–AG05 to slot 0–5 and the lighting model's threadKey
  -> CodexMicroServiceManager.handleHidEvent
     checks session lock and routes to the primary/owning app window
  -> codex-micro-hid-event (Electron window message)
  -> renderer keyboard bridge
     handles menus/custom controls, then may select the referenced task
```

The service takes the task identity from its displayed lighting model, or the
latest model during initial/control-plane recovery. This is better evidence
than guessing from a database's newest activity. The renderer also computes
`selectedThreadKey` for lighting. Neither value was found exported as a supported
external selection feed.

The native event's `act === 1` is treated as a press. Its `key`, rather than the
optional `agent` field, determines the slot. In the currently inspected code,
local task keys use the `local:` prefix. The resolved event target must agree
with a fresh roster; a numeric slot alone is insufficient after a pin reorder.

Current six local office pins, verified read-only from the task database:

| Device key | Native slot | Office key | Pinned task |
| --- | ---: | ---: | --- |
| AG00 | 0 | 1 | Scout (Mr Manager) |
| AG01 | 1 | 2 | Bolt (Product SWE) |
| AG02 | 2 | 3 | Pixel (AI Art Curator) |
| AG03 | 3 | 4 | Echo (Comms) |
| AG04 | 4 | 5 | Atlas (Travel Agent) |
| AG05 | 5 | 6 | Nova (Pick up player) |

This table describes the local office roster, not a newly verified hardware
configuration. The actual Micro assignment mode could differ. The desktop can
also flatten pinned projects and remote tasks into its resolved six slots; the
office's local SQL query does not reproduce that broader mapping.

[OpenAI's Micro guide](https://learn.chatgpt.com/docs/features/codex-micro)
documents Pinned chats, recent/priority/custom assignments, and input contexts
where an Agent Key cancels a composer menu. Thus **a physical press is a target
candidate, not proof that the selected task changed**. Successful selection
should come from a resulting app selection event, with origin metadata if the
spotlight specifically means “selected by the physical keyboard.”

## What was actually exercised

| Check | Observed result | Limit |
| --- | --- | --- |
| Native IORegistry inventory | No interfaces matching vendor 12346 and supported product IDs 33632, 33431, or 33432 | No physical Micro was available on this Mac; does not exclude a keyboard connected to another Mac |
| Existing Codex Unix socket | Initialization succeeded in **0.699 ms** | This is local socket handshake latency, not key-to-spotlight latency |
| Bounded live observation | **15.002 seconds**, no disconnect | One session; not a long-running reliability claim |
| Received messages | 1 initialization response, 1 capability discovery request, 7 `thread-stream-following-changed` broadcasts | These broadcasts mean task-stream subscriptions, not selected-task changes |
| Observer transmissions | 1 initialization request, 1 negative capability reply, 0 subscriptions | No key injection, navigation, HID opening, or prompts |
| Physical key presses measured | **0** | Latency, missed/duplicate press rate, and USB/Bluetooth behavior remain unmeasured |
| Prototype tests | **15 passed**, including a real temporary Unix-socket test | Socket test messages and replay events are synthetic |

The observer deliberately does not interpret stream following, unread changes,
task start/finish, or manager activity as keyboard selection. Other clients can
subscribe to tasks in the background, including Agent Office itself.

The external Unix-socket router forwards broadcasts that clients actually send.
The inspected Micro path uses a window message; no Micro broadcast emission was
found in that path. The 15-second observation alone cannot prove that an event
never exists, particularly with no connected device.

## Repeat the checks

Run from the isolated worktree root with Python 3.9 or newer on macOS:

```sh
# Enumerate only supported Micro interfaces; never opens HID handles.
python3 prototypes/keyboard-selection/probe.py inventory

# Read six local office pins, emitting hashed tokens instead of private IDs.
python3 prototypes/keyboard-selection/probe.py pins

# Fingerprint relevant installed source and list matching markers.
# No application source is copied into this repository or printed.
python3 prototypes/keyboard-selection/probe.py inspect-app

# Observe existing IPC for up to 60 seconds; only method counts are retained.
python3 prototypes/keyboard-selection/probe.py observe-ipc --seconds 15

# Synthetic mapping example: first key, sixth key, ignored release.
python3 prototypes/keyboard-selection/probe.py replay prototypes/keyboard-selection/example-events.json

# Parser, mapping, privacy, roster, archive, and temporary-socket tests.
python3 -m unittest discover -s prototypes/keyboard-selection -p 'test_*.py' -v

git diff --check
```

`--codex-dir PATH` is available for `pins` and `observe-ipc`; `inspect-app`
accepts `--asar PATH`. Paths are read-only. Run on the same host as the desktop
and keyboard. The IPC command returns immediately on a missing or inaccessible
socket, and clears the connection when its deadline ends. It makes no durable
registration and does not accept discovery requests to perform work.

The replay fixture contains invented task identifiers. Its candidates always
have `selectionConfirmed: false`. The one-second maximum roster age in the
mapping helper is a conservative prototype validation rule, not a measured
device requirement. An eventual live adapter must verify current mode, resolved
slots, device presence, app ownership, and selected task independently.

## Source evidence and reproducibility

Manually inspected inside `/Applications/ChatGPT.app/Contents/Resources/app.asar`:

| Source | Evidence |
| --- | --- |
| `.vite/build/service-z8uGrRiL.js` | Supported vendor/product IDs; AG00–AG05 parsing; HID subscription; slot-to-lighting-model task mapping |
| `.vite/build/main-C5K7o1Hr.js` | Owner-window routing, session-lock filtering, double-tap behavior; sends `codex-micro-hid-event` to the window |
| `webview/assets/codex-micro-bridge-7749dc2a7114.js` | Renderer receives the event; handles control/menu contexts before task navigation |
| `webview/assets/codex-micro-slot-signals-1d809a417a41.js` | Six-slot resolution for pinned/recent/priority/custom modes and selected-task lighting |
| `webview/assets/app-initial-86767c3d23e5.js` | Local/remote task-key prefixes |
| `.vite/build/src-VqXTPopo.js` | External socket initialization, routing, broadcast version table |
| `node_modules/@worklouder/device-kit-oai/README.md` and HID type declarations | Nonexclusive HID connection permission notes and key notification fields |

`inspect-app` emits SHA-256 hashes of the five principal source modules as a
repeatable comparison. For this build, the service hash was
`b250fbfeb12e6fb699afc59c9b490c60a855d2d419305142839b3468396940c4`;
the host module hash was
`1a12a4625ca931b86befd0c40e3ab1b17355bbb8ef87092b1d239e0439ce0d26`.
These are implementation observations, not promises of compatibility with a
future desktop release. The marker scan is a starting point for inspection, not
an automatic verdict that a signal is available.

## Permissions, failure modes, and next experiment

Inventory and IPC observation worked under the normal macOS user outside the
agent's filesystem/network sandbox. The sandbox blocked `ioreg -a` and creating
a test Unix socket; the same commands passed under approved native execution.
No macOS privacy setting was changed, and no Input Monitoring or Accessibility
permission was requested by this prototype.

The shipped Work Louder SDK documents nonexclusive HID connections and requires
Input Monitoring for the host process that opens one. The desktop's own service
also writes lighting during connection, so running that service a second time
would not meet the read-only experiment's scope. Shared-device read reliability
has not been tested. A future direct observer would need to be restricted to the
vendor interface, avoid all writes/device mode changes, and establish that it
does not interfere with the app. System-wide key logging is unnecessary.

| Condition | Required behavior for a future adapter |
| --- | --- |
| Device missing, disconnected, on another host, or wrong connection mode | Selection unavailable; no retained spotlight presented as current |
| Press targets an unassigned/custom-action slot or is consumed by a menu | Do not claim a task selection |
| Pinned order changes, displayed lighting lags, or resolved map disagrees | Reject the candidate and refresh the authoritative map |
| Nonlocal task, pinned project, or alternate key mode | Use the actual desktop resolved slot map; never infer from six local SQL rows |
| App locks, owner window changes, or multiple windows compete | Clear selection or resolve explicit ownership |
| Repeated same-key tap | Preserve valid press events; do not apply a blanket debounce that hides double taps |
| Connection/format/version change | Clear stale state and report unavailable |

Preferred next experiment: obtain an authorized read-only event source from the
desktop that emits **successful selected-task changes**, along with the resolved
six-slot map and whether the change came from a Micro key. That keeps the app as
the sole device owner and avoids duplicate HID handling. No production source
for that proposed event is implemented here.

If that is unavailable, revise the feature to an explicitly page-local
spotlight. A separate HID observer remains an experiment, not a release-ready
fallback.

Before approving physical-key behavior, connect the Micro to this Mac, verify
Pinned chats in the desktop, and manually exercise all six keys, double taps,
menu-open presses, pin reordering, lock/unlock, reconnect, and another app in
front. Record at least 10 presses per key with observed navigation and the
candidate feed, then calculate missing/duplicate events and end-to-end latency.
Any timing from hand-entered timestamps must be labeled approximate. The present
probe does **not** supply the missing renderer event feed, even if a keyboard is
connected later.

**User action/approval actually needed:** none to integrate these documentation
and probe files. Direct physical verification needs the keyboard connected to
the same Mac and someone to press it. A future additional HID-reading process
may need its own Input Monitoring grant; do not change that permission or remap
keys as part of this branch. No approval is requested for hypothetical settings.
