"""Extract recent manager communication metadata, never message contents."""
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

MAX_READ = 1024 * 1024
EVENT_LIFETIME = 45


def communication_events(record, manager_id, worker_ids, now):
    if not isinstance(record, dict) or record.get('type') != 'event_msg':
        return []
    payload = record.get('payload', {})
    if not isinstance(payload, dict) or payload.get('type') != 'item_completed':
        return []
    item = payload.get('item', {})
    if not isinstance(item, dict) or item.get('type') != 'McpToolCall' or item.get('server') != 'codex_app' or item.get('status') != 'completed':
        return []
    result = item.get('result')
    if isinstance(result, dict) and result.get('isError'):
        return []
    tool = item.get('tool')
    if tool not in ('list_threads', 'read_thread', 'wait_threads', 'send_message_to_thread'):
        return []
    try:
        at = datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).timestamp()
    except (KeyError, ValueError, TypeError, AttributeError):
        return []
    if not now - EVENT_LIFETIME <= at <= now + 2:
        return []
    args = item.get('arguments', {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return []
    if not isinstance(args, dict):
        return []
    if tool == 'list_threads':
        targets, kind = [None], 'overview'
    elif tool == 'wait_threads':
        requested = args.get('targets', [])
        if not isinstance(requested, list):
            return []
        targets = [target.get('threadId') for target in requested
                   if isinstance(target, dict) and target.get('hostId', 'local') == 'local']
        kind = 'check'
    else:
        targets = [args.get('threadId')] if args.get('hostId', 'local') == 'local' else []
        kind = 'message' if tool == 'send_message_to_thread' else 'check'
    events = []
    for target in dict.fromkeys(value for value in targets if value is None or isinstance(value, str)):
        if kind != 'overview' and target not in worker_ids:
            continue
        signature = f"{manager_id}:{item.get('id')}:{record['timestamp']}:{kind}:{target}"
        events.append({'id': hashlib.sha256(signature.encode()).hexdigest()[:24],
                       'from': manager_id, 'to': target, 'kind': kind, 'at': at})
    return events


class CommunicationFeed:
    def __init__(self, codex_dir, roster_path):
        self.codex_dir = Path(codex_dir).expanduser().resolve()
        self.roster_path = Path(roster_path)
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.events = {}

    def reset(self):
        self.identity = None
        self.offset = 0
        self.pending = b''
        self.events.clear()

    def snapshot(self, rows, now=None):
        now = time.time() if now is None else now
        try:
            with self.roster_path.open('rb') as stream:
                roster = json.loads(stream.read(65536))
            manager_id = roster['managerThreadId']
            pinned = {row['id']: row for row in rows}
            worker_ids = {member['threadId'] for member in roster['members']
                          if member.get('hostId', 'local') == 'local'
                          and member['threadId'] != manager_id and member['threadId'] in pinned}
            if manager_id not in pinned or not worker_ids:
                self.reset()
                return []
            path = Path(pinned[manager_id]['rollout_path']).resolve()
            path.relative_to(self.codex_dir / 'sessions')
            metadata = path.stat()
            identity = (manager_id, str(path), metadata.st_ino)
            skip_first = False
            if identity != self.identity or metadata.st_size < self.offset:
                self.reset()
                self.identity = identity
                self.offset = max(0, metadata.st_size - MAX_READ)
                skip_first = self.offset > 0
            with path.open('rb') as stream:
                stream.seek(self.offset)
                chunk = stream.read(MAX_READ)
                self.offset = stream.tell()
            if skip_first:
                chunk = chunk.partition(b'\n')[2]
            lines = (self.pending + chunk).split(b'\n')
            self.pending = lines.pop()
            if len(self.pending) > MAX_READ:
                self.pending = b''
            for line in lines:
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                for event in communication_events(record, manager_id, worker_ids, now):
                    self.events[event['id']] = event
            self.events = {key: event for key, event in self.events.items()
                           if now - EVENT_LIFETIME <= event['at'] <= now + 2
                           and (event['to'] is None or event['to'] in worker_ids)}
            return sorted(self.events.values(), key=lambda event: event['at'])[-32:]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.reset()
            return []
