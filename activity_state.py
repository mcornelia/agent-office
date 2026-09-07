"""Bounded lifecycle reader and disposable, private restart checkpoints.

Only lifecycle metadata and byte fingerprints reach disk. These checkpoints
are observations, never job authorizations, send receipts, or approval state.
"""
import hashlib
import json
import os
import stat
import tempfile
import time
from datetime import datetime
from pathlib import Path

MAX_TAIL = 8 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024
MAX_EVENT_BYTES = 64 * 1024
ANCHOR_BYTES = 1024
STATES = {"task_started": "working", "task_complete": "complete",
          "turn_aborted": "interrupted", "error": "error"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.tzinfo is not None and parsed.timestamp() <= time.time() + 5
    except (ValueError, OverflowError):
        return False


def event_state(line):
    item = json.loads(line)
    if not isinstance(item, dict):
        raise ValueError('Invalid event record')
    if item.get('type') != 'event_msg':
        return None
    payload = item.get('payload')
    if not isinstance(payload, dict):
        raise ValueError('Invalid lifecycle payload')
    state = STATES.get(payload.get('type'))
    if state:
        if len(line) > MAX_EVENT_BYTES or not stamp(item.get('timestamp')):
            raise ValueError('Invalid lifecycle event')
        return state, item['timestamp']
    return None


def anchors(stream, offset):
    stream.seek(0)
    head = stream.read(min(ANCHOR_BYTES, offset))
    stream.seek(max(0, offset - ANCHOR_BYTES))
    tail = stream.read(min(ANCHOR_BYTES, offset))
    return digest(head), digest(tail)


class ActivityCheckpoints:
    """At most six entries; load/save failures never disable live observation."""
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.entries = {}
        self.last_saved = None
        if self.path:
            try:
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or info.st_mode & 0o077 or info.st_size > MAX_CHECKPOINT_BYTES):
                        return
                    data = json.loads(stream.read(MAX_CHECKPOINT_BYTES + 1))
                entries = data.get('entries')
                if data.get('version') == 1 and isinstance(entries, dict) and len(entries) <= 6:
                    self.entries = entries
            except (OSError, ValueError, AttributeError):
                pass

    @staticmethod
    def key(task_id, path):
        return digest(json.dumps([task_id, str(path)], separators=(',', ':')).encode())

    def save(self, entries):
        # Replace only this disposable cache, never a manager ledger/receipt.
        self.entries = entries
        if not self.path or len(entries) > 6:
            return
        temporary = None
        try:
            body = (json.dumps({'version': 1, 'entries': entries}, sort_keys=True,
                               separators=(',', ':'), allow_nan=False) + '\n').encode()
            if len(body) > MAX_CHECKPOINT_BYTES or body == self.last_saved:
                return
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077):
                    return
            fd, temporary = tempfile.mkstemp(prefix='.activity-', suffix='.tmp', dir=self.path.parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.last_saved = body
        except (OSError, ValueError, TypeError):
            pass
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


class EventTail:
    def __init__(self, path, checkpoint=None):
        self.path = Path(path)
        self.offset = 0
        self.identity = None
        self.pending = b''
        self.discarding = False
        self.state = 'unknown'
        self.event_at = None
        self.event_offset = self.event_length = 0
        self.checkpoint = checkpoint
        self.record = None

    def forget(self):
        self.state, self.event_at = 'unknown', None
        self.record = None

    def valid_record(self, stream, info, record):
        try:
            numbers = ['device', 'inode', 'offset', 'mtimeNs', 'eventOffset', 'eventLength']
            if any(type(record[k]) is not int or record[k] < 0 for k in numbers):
                return False
            offset = record['offset']
            if ((record['device'], record['inode']) != (info.st_dev, info.st_ino)
                    or not 0 < offset <= info.st_size or info.st_mtime_ns < record['mtimeNs']
                    or (offset == info.st_size and info.st_mtime_ns != record['mtimeNs'])
                    or not 0 < record['eventLength'] <= MAX_EVENT_BYTES
                    or record['eventOffset'] + record['eventLength'] > offset):
                return False
            if anchors(stream, offset) != (record['head'], record['anchor']):
                return False
            stream.seek(offset - 1)
            if stream.read(1) != b'\n':
                return False
            stream.seek(record['eventOffset'])
            line = stream.read(record['eventLength'])
            return (digest(line) == record['eventHash'] and
                    event_state(line) == (record['state'], record['eventAt']))
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            return False

    def read(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise OSError('History must be a regular file')
            identity = (info.st_dev, info.st_ino)
            if self.identity != identity or info.st_size < self.offset:
                candidate, self.checkpoint = self.checkpoint, None
                self.forget()
                self.identity, self.pending = identity, b''
                if candidate and self.valid_record(stream, info, candidate):
                    self.offset = candidate['offset']
                    self.state, self.event_at = candidate['state'], candidate['eventAt']
                    self.event_offset, self.event_length = candidate['eventOffset'], candidate['eventLength']
                    self.record = candidate
                    self.discarding = False
                else:
                    self.offset = max(0, info.st_size - MAX_TAIL)
                    if self.offset:
                        stream.seek(self.offset - 1)
                        self.discarding = stream.read(1) != b'\n'
                    else:
                        self.discarding = False
            elif self.record and not self.valid_record(stream, info, self.record):
                # In-place edits (including truncate/regrow) invalidate the cursor.
                self.identity = None
                self.checkpoint = None
                self.forget()
                return 'unknown', None

            start = self.offset - len(self.pending)
            stream.seek(self.offset)
            data = self.pending + stream.read(MAX_TAIL)
            self.offset = stream.tell()
            self.pending = b''
            if self.discarding:
                end = data.find(b'\n')
                if end < 0:
                    return 'unknown', None
                start += end + 1
                data = data[end + 1:]
                self.discarding = False
            lines = data.split(b'\n')
            self.pending = lines.pop()
            for part in lines:
                line = part + b'\n'
                try:
                    result = event_state(line) if len(line) <= MAX_TAIL else None
                    if len(line) > MAX_TAIL:
                        self.forget()
                    elif result:
                        self.state, self.event_at = result
                        self.event_offset, self.event_length = start, len(line)
                except (ValueError, UnicodeDecodeError, TypeError):
                    self.forget()
                start += len(line)
            if len(self.pending) > MAX_TAIL:
                self.pending = b''
                self.discarding = True
                self.forget()

            final = os.fstat(stream.fileno())
            current = self.path.stat()
            if ((current.st_dev, current.st_ino) != identity or final.st_size < self.offset
                    or (final.st_size == info.st_size and final.st_mtime_ns != info.st_mtime_ns)):
                self.identity = None
                self.forget()
                return 'unknown', None
            # Never expose a cached idle/completed state ahead of unread data.
            if self.offset < final.st_size or self.pending or self.discarding:
                return 'unknown', None
            if self.state != 'unknown':
                head, anchor = anchors(stream, self.offset)
                stream.seek(self.event_offset)
                self.record = {'device': final.st_dev, 'inode': final.st_ino,
                               'offset': self.offset, 'mtimeNs': final.st_mtime_ns,
                               'head': head, 'anchor': anchor, 'state': self.state,
                               'eventAt': self.event_at, 'eventOffset': self.event_offset,
                               'eventLength': self.event_length,
                               'eventHash': digest(stream.read(self.event_length))}
            # A restart must not rejuvenate abandoned work or imply approval state.
            if self.state == 'working' and time.time() - final.st_mtime > 300:
                return 'unknown', self.event_at
            return self.state, self.event_at
