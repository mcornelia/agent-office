#!/usr/bin/env python3
"""Prepare private manager configuration from the current local pinned tasks."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from server import OfficeState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager-key', type=int, choices=range(1, 7), default=1)
    parser.add_argument('--codex-dir', type=Path, default=Path.home()/'.codex')
    args = parser.parse_args()
    team_path, brief_path = ROOT/'team.json', ROOT/'MANAGER-BRIEF.md'
    if team_path.exists() or brief_path.exists():
        parser.error('Local configuration already exists; review it before changing the manager or roster.')
    state = OfficeState(args.codex_dir)
    try:
        connection = state.connect()
        try:
            rows = state.pinned(connection, state.app_state())
        finally:
            connection.close()
    except Exception as exc:
        parser.error(f'Could not read local pins: {exc}')
    if len(rows) < args.manager_key:
        parser.error('Pin the manager task in Codex before configuring the office.')
    manager = rows[args.manager_key - 1]
    team = {'schemaVersion': 1, 'effectiveAt': datetime.now(timezone.utc).isoformat(),
            'codexDir': str(args.codex_dir.expanduser().resolve()), 'managerThreadId': manager['id'],
            'members': [{'threadId': row['id'], 'hostId': 'local', 'titleAtSetup': row['title'],
                         'role': 'manager' if row['id'] == manager['id'] else 'worker', 'keyAtSetup': i+1}
                        for i, row in enumerate(rows)]}
    brief = (ROOT/'MANAGER-BRIEF.template.md').read_text().replace('{{ROOT}}', str(ROOT.parent)).replace('{{MANAGER_NAME}}', manager['title'])
    # Exclusive creation preserves existing local configuration, even after a race.
    for path, value in ((team_path, json.dumps(team, indent=2)+'\n'), (brief_path, brief)):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(value)
    print(f'Prepared private configuration for {manager["title"]}.')
    print(f'Read {brief_path} in the manager task and authorize the workflow there.')
    print('No task was messaged and no automation was installed.')


if __name__ == '__main__':
    main()
