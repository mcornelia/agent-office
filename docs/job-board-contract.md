# Read-only job board contract (v1)

`job_board.JobBoardFeed` projects the existing `manager/team.json` roster and
`manager/state.json` job ledger into the whiteboard, Needs You tray, and results
shelf. The machine contract is [`job-board.schema.json`](job-board.schema.json).
No server routes, UI files, source ledgers, or local Codex data are modified.

## Integration

Create one feed for the server lifetime; do not construct one per poll:

```python
from job_board import JobBoardFeed

board = JobBoardFeed(ROOT / 'manager' / 'team.json')
# Inside the existing snapshot flow, after producing the six current slots:
runtime = {slot['id']: desktop_status.get(slot['id'])
           for slot in slots if slot.get('id')}
payload['jobBoard'] = board.snapshot(slots, runtime)
```

Pass `None` for unavailable desktop statuses. Do not turn absence into `idle`.
The feed never reads session transcripts or raw desktop snapshots. It does not
need `manager/read_task.py`. Its source path arguments are trusted startup
configuration, not HTTP request parameters. Its lock protects concurrent polls.

`agents` are roster-scoped, sorted by current key. `id` is a stable generated
presentation token; it is **not** a thread ID usable by a Codex API. Match a
pinned agent to the existing UI by `key`; `avatar` preserves character identity.
An unpinned roster member has `key: null` and can still have pending requests.
No private task, turn, cursor, or follow-up identifiers are returned.

Each `assignment` is the newest registered unfinished job for that agent, or
`null`. `stage` is `researching`, `building`, `testing`, `waiting`, or `unknown`.
Only explicit ledger stage evidence is used. A live approval/input wait overrides
that stage; animation, task titles, private messages, quietness, and generic
`active`/`idle` states do not establish research, testing, or job completion.
An older job's retained request does not change a newer job's displayed stage.

## Manager input and explicitly safe display copy

The existing ledger shape remains supported: `jobs[]` rows identify their owner
with `taskId`, identity with `sourceTurnId` (or explicit `id`, then
`sourceTimestamp`), and progress with `lastObservedStatus`. Rows without a stable
identity or outside the local roster are ignored. `workers` baseline history
does not register assignments. Use `lastCheckAt` for source freshness; optional
per-job `updatedAt`/`lastObservedAt` preserves older-job staleness.

Private `objective`, `evidence`, `nextStep`, `blocker` contents, raw titles,
cursors, and signatures are **never** copied into the response. For useful
public-facing labels, the manager may deliberately author these optional fields:

```json
{
  "taskId": "internal-worker-id",
  "sourceTurnId": "internal-assignment-turn",
  "lastObservedStatus": "awaiting_user_review",
  "blocker": "Private decision evidence stays here",
  "presentation": {
    "title": "Office job whiteboard",
    "needTitle": "Choose the board layout",
    "action": "Open the task and choose the compact or expanded layout.",
    "summary": "Read-only job data and regression tests are ready.",
    "artifacts": [
      {"label": "Review changes", "url": "https://github.com/example/office/pull/7", "public": true}
    ]
  }
}
```

Roster members can similarly supply `presentation: {"label": "Nova"}`. Without
it, the label is a generic `Agent N`; `titleAtSetup` is intentionally not exposed.
Without safe job copy the UI gets generic actionable text, not a guessed summary.
Presentation fields are an explicit publication boundary: their authors must
write safe summaries, not paste private messages. Regex defenses redact common
paths, emails, credentials, markup, and internal identifiers but cannot recognize
all sensitive natural-language facts. Render labels as text (`textContent`), never
HTML. The schema disallows arbitrary extra response fields.

## Needs You persistence and results

- A non-null blocker or an explicit user/approval-wait status creates a request.
  Plain `waiting` means a stage only; it cannot prove the user owes a decision.
- One request per tracked job deduplicates ledger and live status evidence.
  A later explicit null blocker plus a recognized non-waiting state, a valid
  `resolvedAt` timestamp, or a
  completed/resolved/cancelled job clears the **ledger** request. A live active
  snapshot with an explicit valid `activeFlags` list without wait flags, or
  `idle`, clears the **desktop** request. Missing or malformed flags do not clear
  a request. Live flags do not overwrite explicitly authored safe manager copy.
  If both sources contributed, each must clear; a stale manager request is not
  silently approved by the viewer. Open the task to act; this feed has no write API.
- Age, a dropped row, `notLoaded`, partial writes, source errors, or disconnects
  do not dismiss pending requests. They remain in the long-lived feed with stale
  status. A new job's completion does not resolve an older job's blocker.
- Durable persistence belongs to the manager ledger: keep unresolved job rows
  and completion records there across viewer restarts. A read-only observer
  cannot recover an omitted request after its own process restart. Runtime-only
  waits persist in memory and reconstruct from the next authoritative live
  snapshot; no requests or transcripts are written to disk.
- Only completed jobs produce results. They are stable-ID deduplicated, newest
  first, capped at 100, retained during temporary source loss/ledger compaction,
  and retracted if that same job is explicitly reopened. Provide `completedAt`
  for precise ordering; otherwise the first observed completion's ledger time
  is used. Unknown completion dates remain `null`.

## Artifact links and degraded sources

Only explicit `presentation.artifacts` entries with `public: true` are considered.
External links allow HTTP(S) on public-looking DNS hostnames with default ports;
userinfo, query strings, fragments, private addresses/hostnames, sensitive paths,
traversal, unsafe schemes, and identifiers from that job are rejected. They have
`status: "unverified"`: no network request checks destination content or HTTP
availability, and the UI must not call them verified/live links.

Local artifacts are hidden (`href: null`) in default presentation mode. A trusted
local-only caller can opt into `presentation_mode=False, artifact_roots=[...]`.
Then a local `path` must be absolute, explicitly public, resolve to a regular
allowed document/image file under an approved project root, and contain no hidden
path components. Symlink escapes are refused; missing files are marked `missing`.
Only this opted-in mode emits `file:` links (which some browsers do not open).
The feed never reads artifact contents and never serves arbitrary files. Recheck
link status on each poll; cached local links are also checked after source loss.

The source budget is 1 MiB per JSON file, six roster members, and 500 ledger jobs.
Malformed, oversized, unsupported, future-dated, and out-of-order sources fail
closed with generic issue codes, never exception text or filesystem paths.
Older per-job timestamps in a newer ledger snapshot are also rejected while the
previous job remains cached. The manager must retain current evidence; this
read-only observer is not a durable version archive after restart/compaction.
`source.available` describes current reads; `source.stale` also reflects unknown
timestamps or the default 30-minute freshness threshold. Stale work stages become
`unknown`; unresolved requests do not expire. Keep a visible stale-data indicator.
Removing a member from the roster removes their cached presentation data from
view; it does not resolve or alter their actual task.

## Checks

```sh
python3 -m unittest -v test_job_board.py
python3 -m unittest -v test_server.py test_desktop_status.py test_communications.py
python3 -m unittest discover -s manager -p 'test_*.py' -v
```

Tests use synthetic sources only. They cover explicit/unknown stages, stale and
missing sources, duplicated jobs and requests, approval persistence/clearing,
completed results, root escapes and broken links, source immutability, and
privacy-safe presentation. No live main checkout or private ledger is required.
Every snapshot produced by the shared test fixture is also checked against all
keywords used in the JSON schema, using a dependency-free contract checker.
