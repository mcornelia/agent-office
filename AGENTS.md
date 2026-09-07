# Agent Office working agreements

This handbook applies to Agent Office only. It is project guidance, not a new
assignment, a team identity, or permission to start rounds, delegate work, or
publish. Paths below are relative to this repository.

## Read the right source

- Existing team members explicitly onboarded by the user read their assigned
  [role guide](team/README.md), not every guide or a role guessed from their name.
- Start with [README.md](README.md) for architecture and setup.
- Before changing the gate, recovery, or ledger ownership, or operating an
  authorized manager workflow, read [manager/CONTINUITY.md](manager/CONTINUITY.md).
- For coordinator rounds or changes to their behavior, read
  [manager/COORDINATOR.md](manager/COORDINATOR.md). It applies as an operating
  role only to the existing task selected in the private configuration.
- The configured foreground owner also reads its private manager brief when
  present. Other contributors do not inherit that role. Missing private files
  are normal in a clone; do not fabricate identities or recreate household data.

Keep detailed policy in those sources, not in duplicate handbooks. The handbook
describes working agreements, private ledgers record progress, and the local
gate decides whether an automatic request is eligible. Instructions cannot
replace the gate's checks or grant additional user authorization.

## Work safely

- Inspect the worktree; preserve others' edits and any assigned file/worktree
  ownership. Do not interrupt progressing teammates or start duplicate work.
- Identify operating roles through authorized configuration, never a task
  name. In split mode, the coordinator owns the team ledger and the foreground
  owner owns its checkpoint file, as specified in the continuity contract.
- Save actual progress before yielding an authorized foreground job. Preserve
  approval waits and newer user decisions. Do not infer a new assignment from
  old history, an idle flag, or this handbook.
- Keep roster IDs, transcripts, account details, private paths, and ledgers out
  of Git, fixtures, screenshots, and public dashboard data. Use synthetic tests.
- Edit `office.html` or `page-shell.html`, then regenerate `index.html` with
  `python3 build_view.py`; do not hand-edit the generated page.
- Live installs, restarts, automation changes, and GitHub publication need
  applicable user authorization. Preserve the current office until a tested
  change and backout path are ready. Report staged, local-live, and published
  states separately. Never restore stale ledgers or send receipts during backout.

## Validation

For code changes, run these from the repository root; CI is defined in
[.github/workflows/checks.yml](.github/workflows/checks.yml):

```sh
python3 -m unittest discover -q
python3 -m unittest discover -s manager -p 'test_*.py' -q
python3 -m unittest discover -s desktop -p 'test_*.py' -q
python3 -m unittest discover -s prototypes/keyboard-selection -p 'test_*.py' -q
node --test test_rounds.cjs test_polling.cjs
python3 build_view.py
git diff --check
```

Check that generated output matches its sources. For UI changes also check the
affected states in a browser, including mobile and reduced motion where relevant.
For documentation-only changes, verify links, instructions, scope, and examples;
do not restart the service just to validate text.

## Code Review Rules

- Flag status that turns missing, stale, or disconnected evidence into idle,
  completion, or permission. Preserve explicit unknown/reconnecting states.
- Flag any execution path that skips durable pre-send reservations, resets
  per-job authorization budgets, retries uncertain sends, or resumes a stale
  checkpoint. Recovery requires human review; do not reintroduce automatic
  recovery through ordinary task permissions. Keep approval boundaries intact.
- Flag concurrent ledger ownership or coordinator nudges into foreground work
  in split mode. Follow the separate-owner contract.
- Flag private data escaping through viewer projections or new HTTP routes.
  Keep public fields allowlisted; the viewer is not a management/write API.
