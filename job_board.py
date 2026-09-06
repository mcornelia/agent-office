"""Read-only manager-ledger projection for the office's presentation surfaces.

No transcripts, tool arguments, prompts, database writes, or network requests.
This module does not authorize work or resolve requests on the user's behalf.
"""
import copy
import hashlib
import ipaddress
import json
import math
import re
import stat
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

MAX_SOURCE_BYTES = 1024 * 1024
MAX_JOBS = 500
STAGES = {'researching', 'building', 'testing', 'waiting'}
ACTIVE = (STAGES - {'waiting'}) | {'active', 'working', 'inprogress', 'in_progress'}
COMPLETED = {'complete', 'completed', 'done'}
RESOLVED = COMPLETED | {'resolved', 'cancelled', 'canceled', 'dismissed'}
APPROVAL = {'awaiting_approval', 'waiting_for_approval', 'needs_approval'}
INPUT = {'awaiting_user_review', 'awaiting_user_input', 'waiting_for_input', 'needs_input'}
LOCAL_EXTENSIONS = {'.md', '.txt', '.pdf', '.png', '.jpg', '.jpeg', '.webp',
                    '.csv', '.tsv', '.xlsx', '.docx', '.pptx'}


def _id(kind, *parts):
    encoded = json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode()
    return kind + '-' + hashlib.sha256(encoded).hexdigest()[:24]


def _timestamp(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if value.tzinfo is None:
                return None
            value = value.timestamp()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        datetime.fromtimestamp(value, timezone.utc)
        return float(value)
    except (ValueError, OverflowError, OSError):
        return None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def public_text(value, fallback, limit=160, private_tokens=()):
    """Defense in depth for *explicit* presentation copy, not a PII classifier.

    Never call this on objectives, evidence, next steps, or conversation text.
    Their privacy cannot be established by regex redaction.
    """
    if not isinstance(value, str):
        return fallback
    text = unicodedata.normalize('NFKC', value[:4096])
    text = ''.join(' ' if c.isspace() else c for c in text if c.isspace() or unicodedata.category(c) not in ('Cc', 'Cf'))
    for token in sorted(set(t for t in private_tokens if isinstance(t, str) and t), key=len, reverse=True):
        text = text.replace(token, '[private]')
    text = re.sub(r'(?i)\b(?:https?://|file://)\S+', '[link]', text)
    text = re.sub(r'(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|~/|/)[^\s<>"\']+', '[path]', text)
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[private]', text)
    text = re.sub(r'(?i)\b(?:api[-_ ]?key|token|password|secret|authorization)\s*[:=]\s*\S+', '[private]', text)
    text = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[private]', text)
    text = re.sub(r'\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+', '[private]', text)
    text = re.sub(r'\b[A-Za-z0-9_-]{48,}\b', '[private]', text)
    text = re.sub(r'<[^>]*>', '', text)
    text = ' '.join(text.split()).strip()
    return text[:limit] or fallback


def _read_json(path):
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_SOURCE_BYTES:
        raise ValueError('unsupported source')
    with path.open('rb') as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('oversized source')
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get('schemaVersion', 1)) is not int or data.get('schemaVersion', 1) != 1:
        raise ValueError('unsupported source schema')
    return data


def _external_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\\x00-\x1f]', value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme not in ('http', 'https') or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.port not in (None, 80 if parsed.scheme == 'http' else 443)
                or not re.fullmatch(r'[A-Za-z0-9.-]+', host) or '.' not in host
                or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.test', '.invalid'))
                or not re.fullmatch(r'[A-Za-z]{2,63}', host.split('.')[-1])
                or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                       for label in host.split('.'))):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        decoded = unquote(unquote(parsed.path))
        if (re.search(r'%(?![0-9a-fA-F]{2})', parsed.path)
                or any(part.startswith('.') or part == '~' for part in decoded.split('/') if part)
                or re.search(r'(?i)/(?:users|home|private|volumes|etc|proc|sys)/', decoded)
                or re.search(r'[\\<>"\x27`\x00-\x1f]', decoded)):
            return None
        return urlunsplit((parsed.scheme, host.lower(), parsed.path or '/', '', ''))
    except (ValueError, UnicodeError):
        return None


class JobBoardFeed:
    """One long-lived instance per server; source paths are operator configuration.

    snapshot(slots, runtime_by_id=None) takes existing OfficeState slots plus
    optional sanitized DesktopStatus values keyed by private thread ID. Neither
    input is returned verbatim. Missing desktop status is NOT a resolved request.
    """
    def __init__(self, roster_path, ledger_path=None, *, stale_after=1800,
                 presentation_mode=True, artifact_roots=()):
        self.roster_path = Path(roster_path)
        self.ledger_path = Path(ledger_path) if ledger_path else self.roster_path.with_name('state.json')
        self.stale_after = stale_after
        self.presentation_mode = presentation_mode
        self.artifact_roots = tuple(Path(p).expanduser().resolve() for p in artifact_roots)
        self.lock = threading.Lock()
        self.members = {}
        self.jobs = {}
        self.needs = {}
        self.results = {}
        self.ledger_at = None
        self.have_ledger = False

    def _roster(self, data):
        if not isinstance(data.get('members'), list) or len(data['members']) > 6:
            raise ValueError('unsupported roster')
        members = {}
        for member in data['members']:
            if not isinstance(member, dict):
                raise ValueError('unsupported member')
            thread = member.get('threadId')
            if not isinstance(thread, str) or not thread or len(thread) > 256 or thread in members:
                raise ValueError('unsupported identity')
            if member.get('hostId', 'local') != 'local':
                continue
            presentation = member.get('presentation', {})
            if not isinstance(presentation, dict):
                presentation = {}
            members[thread] = {
                'id': _id('agent', thread),
                'label': public_text(presentation.get('label'), 'Agent', 60,
                                     [m.get('threadId') for m in data['members'] if isinstance(m, dict)]),
                'role': 'manager' if thread == data.get('managerThreadId') else 'worker',
            }
        self.members = members

    def _artifacts(self, presentation, job_id, tokens):
        artifacts = presentation.get('artifacts', [])
        if not isinstance(artifacts, list):
            return []
        result = {}
        for artifact in artifacts[:20]:
            if not isinstance(artifact, dict) or artifact.get('public') is not True:
                continue
            raw = artifact.get('url', artifact.get('path'))
            if not isinstance(raw, str) or not raw or len(raw) > 4096:
                continue
            identifier = _id('artifact', job_id, raw)
            item = {'id': identifier, 'label': public_text(artifact.get('label'), 'Artifact', 80, tokens),
                    'kind': 'external' if 'url' in artifact else 'local', 'href': None, 'status': 'unsafe'}
            if 'url' in artifact:
                decoded_url = unquote(unquote(raw))
                item['href'] = None if any(t in decoded_url for t in tokens if isinstance(t, str) and t) else _external_url(raw)
                if item['href']:
                    item['status'] = 'unverified'
            elif self.presentation_mode:
                item['status'] = 'hidden'
            else:
                try:
                    path = Path(raw).expanduser()
                    resolved = path.resolve()
                    allowed = (path.is_absolute() and resolved.suffix.lower() in LOCAL_EXTENSIONS
                               and not any(p.startswith('.') for p in resolved.parts)
                               and any(resolved != root and root in resolved.parents for root in self.artifact_roots))
                    if allowed:
                        item['status'] = 'available' if resolved.is_file() else 'missing'
                        if item['status'] == 'available':
                            item['href'] = resolved.as_uri()
                except (OSError, RuntimeError, ValueError):
                    pass
            result[identifier] = item
        return list(result.values())

    def _normalize_job(self, job, ledger_at, now):
        if not isinstance(job, dict) or job.get('taskId') not in self.members:
            return None
        thread = job['taskId']
        # Do not key by objective text: wording edits must not create new jobs.
        origin = job.get('id') or job.get('sourceTurnId') or job.get('sourceTimestamp')
        if not isinstance(origin, str) or not origin or len(origin) > 512:
            return None
        identity = _id('job', thread, origin)
        tokens = list(self.members) + [job.get(k) for k in
                                      ('id', 'sourceTurnId', 'cursor', 'lastFollowUpSignature', 'lastReportedSignature')]
        tokens.append(origin)
        presentation = job.get('presentation', {})
        if not isinstance(presentation, dict):
            presentation = {}
        status = job.get('lastObservedStatus', job.get('status', 'unknown'))
        if not isinstance(status, str):
            status = 'unknown'
        status = status.lower().replace('-', '_')
        observed = next((at for at in (_timestamp(job.get('updatedAt')),
                                      _timestamp(job.get('lastObservedAt')), ledger_at) if at is not None), None)
        if observed is not None and observed > now + 2:
            raise ValueError('future job')
        resolved_at = _timestamp(job.get('resolvedAt'))
        if resolved_at is not None and resolved_at > now + 2:
            raise ValueError('future resolution')
        completed = status in COMPLETED
        actionable = job.get('blocker') is not None or status in APPROVAL | INPUT
        kind = 'approval' if status in APPROVAL else 'input' if status in INPUT else 'blocker'
        if completed or status in RESOLVED or resolved_at is not None:
            actionable = False
        normalized_status = ('completed' if completed else 'resolved' if status in RESOLVED or resolved_at is not None
                             else 'waiting' if actionable or status == 'waiting'
                             else 'active' if status in ACTIVE
                             else 'error' if status in {'error', 'failed'} else 'unknown')
        return {
            'id': identity, 'thread': thread, 'agentId': self.members[thread]['id'],
            'title': public_text(presentation.get('title'), 'Assigned job', 160, tokens),
            'status': normalized_status, 'stage': 'waiting' if actionable else status if status in STAGES else 'unknown',
            'updated': observed, 'actionable': actionable, 'kind': kind,
            # A missing field is not a clearance. An explicit null blocker and a
            # non-waiting state, or a terminal/resolved state, is clearance.
            'cleared': status in RESOLVED or resolved_at is not None or
                       ('blocker' in job and job['blocker'] is None and status in ACTIVE | {'error', 'failed'}),
            'needTitle': public_text(presentation.get('needTitle'),
                                     'Approval needed' if kind == 'approval' else 'Your input is needed', 120, tokens),
            'action': public_text(presentation.get('action'), 'Open the task to review and respond.', 240, tokens),
            'summary': public_text(presentation.get('summary'), 'Job completed. Open the task to review the result.', 280, tokens),
            'completedAt': _timestamp(job.get('completedAt')) if (_timestamp(job.get('completedAt')) or 0) <= now + 2 else None,
            'artifacts': self._artifacts(presentation, identity, tokens),
        }

    def _ledger(self, data, now):
        if not isinstance(data.get('jobs'), list) or len(data['jobs']) > MAX_JOBS:
            raise ValueError('unsupported jobs')
        at = _timestamp(data.get('lastCheckAt'))
        if at is not None and at > now + 2:
            raise ValueError('future source')
        if self.ledger_at is not None and (at is None or at < self.ledger_at):
            raise ValueError('out-of-order source')
        normalized = {}
        for row in data['jobs']:
            job = self._normalize_job(row, at, now)
            if job is not None:
                old = normalized.get(job['id'])
                if old is None or (job['updated'] or 0) >= (old['updated'] or 0):
                    normalized[job['id']] = job
        for identifier, job in normalized.items():
            previous = self.jobs.get(identifier)
            if previous and previous['updated'] is not None and (job['updated'] is None or job['updated'] < previous['updated']):
                raise ValueError('out-of-order job')
        self.jobs = normalized
        self.ledger_at = at
        self.have_ledger = True

    def _request(self, identifier, agent, job_id, kind, title, action, source, now):
        existing = self.needs.get(identifier)
        if existing is None:
            existing = {'id': identifier, 'agentId': agent, 'jobId': job_id, 'kind': kind,
                        'title': title, 'action': action, 'firstObservedAt': _iso(now), 'sources': []}
            self.needs[identifier] = existing
        # Live wait kind is useful, but must not erase the manager's explicitly
        # authored safe action with generic copy on every poll.
        if source == 'manager_ledger' or 'manager_ledger' not in existing['sources']:
            existing.update(title=title, action=action)
        existing['kind'] = kind
        if source not in existing['sources']:
            existing['sources'].append(source)

    def _clear(self, source, *, agent=None, job_id=None, except_id=None):
        for identifier, request in list(self.needs.items()):
            if (identifier != except_id and (agent is None or request['agentId'] == agent)
                    and (job_id is None or request['jobId'] == job_id)):
                request['sources'] = [s for s in request['sources'] if s != source]
                if not request['sources']:
                    del self.needs[identifier]

    def snapshot(self, slots, runtime_by_id=None, *, now=None):
        now = time.time() if now is None else now
        with self.lock:
            issues = []
            try:
                self._roster(_read_json(self.roster_path))
            except (OSError, ValueError, TypeError, RecursionError):
                issues.append('roster_unavailable')
            try:
                self._ledger(_read_json(self.ledger_path), now)
            except (OSError, ValueError, TypeError, RecursionError):
                issues.append('ledger_unavailable')
            source_stale = bool(issues) or self.ledger_at is None or now - self.ledger_at > self.stale_after
            # Membership bounds visibility; a roster removal is not a decision
            # resolution, and does not mutate the source ledger.
            agent_ids = {m['id'] for m in self.members.values()}
            self.needs = {k: v for k, v in self.needs.items() if v['agentId'] in agent_ids}
            self.results = {k: v for k, v in self.results.items() if v['agentId'] in agent_ids}
            candidates = {}
            for job in self.jobs.values():
                if job['agentId'] not in agent_ids:
                    continue
                if job['cleared']:
                    self._clear('manager_ledger', job_id=job['id'])
                if job['actionable']:
                    self._request(_id('need', job['id']), job['agentId'], job['id'],
                                  job['kind'], job['needTitle'], job['action'], 'manager_ledger', now)
                if job['status'] == 'completed':
                    previous = self.results.get(job['id'])
                    completed_at = next((at for at in (job['completedAt'],
                                                       previous['completedAt'] if previous else None,
                                                       _iso(job['updated'])) if at is not None), None)
                    if isinstance(completed_at, (float, int)):
                        completed_at = _iso(completed_at)
                    self.results[job['id']] = {
                        'id': _id('result', job['id']), 'agentId': job['agentId'], 'jobId': job['id'],
                        'title': job['title'], 'summary': job['summary'], 'completedAt': completed_at,
                        'artifacts': job['artifacts'],
                    }
                else:
                    self.results.pop(job['id'], None)
                    if job['status'] != 'resolved':
                        old = candidates.get(job['thread'])
                        if old is None or (job['updated'] or 0) >= (old['updated'] or 0):
                            candidates[job['thread']] = job
            # A bounded shelf is intentional. Needs You has no age-based expiry.
            self.results = dict(sorted(self.results.items(), key=lambda pair: pair[1]['completedAt'] or '', reverse=True)[:100])
            pinned, used_keys = {}, set()
            for slot in slots if isinstance(slots, list) else []:
                if not isinstance(slot, dict) or slot.get('id') not in self.members:
                    continue
                key = slot.get('key')
                if type(key) is int and 1 <= key <= 6 and key not in used_keys and slot['id'] not in pinned:
                    pinned[slot['id']] = slot
                    used_keys.add(key)
            runtimes = runtime_by_id if isinstance(runtime_by_id, dict) else {}
            agents = []
            for thread, member in self.members.items():
                slot = pinned.get(thread, {})
                job = candidates.get(thread)
                runtime = runtimes.get(thread)
                waiting = False
                if isinstance(runtime, dict):
                    flags = runtime.get('activeFlags')
                    if runtime.get('type') == 'active' and isinstance(flags, list) and all(isinstance(f, str) for f in flags):
                        kind = 'approval' if 'waitingOnApproval' in flags else 'input' if 'waitingOnUserInput' in flags else None
                        waiting = kind is not None
                        if kind:
                            job_id = job['id'] if job else None
                            identifier = _id('need', job_id or member['id'])
                            self._clear('desktop_status', agent=member['id'], except_id=identifier)
                            self._request(identifier, member['id'], job_id, kind,
                                          'Approval needed' if kind == 'approval' else 'Your input is needed',
                                          'Open the task to review and respond.', 'desktop_status', now)
                        else:
                            self._clear('desktop_status', agent=member['id'])
                    elif runtime.get('type') == 'idle':
                        self._clear('desktop_status', agent=member['id'])
                pending = [n for n in self.needs.values() if n['agentId'] == member['id']]
                job_pending = [n for n in pending if job and n['jobId'] == job['id']]
                assignment = None
                if job:
                    stale = source_stale or job['updated'] is None or now - job['updated'] > self.stale_after
                    assignment = {'id': job['id'], 'title': job['title'], 'status': job['status'],
                                  'stage': 'waiting' if waiting or job_pending else 'unknown' if stale else job['stage'],
                                  'stale': stale, 'updatedAt': _iso(job['updated'])}
                    if isinstance(runtime, dict) and runtime.get('type') == 'systemError':
                        assignment.update(status='error', stage='waiting' if job_pending else 'unknown')
                avatar = slot.get('avatar')
                if type(avatar) is not int or not 0 <= avatar <= 5:
                    avatar = None
                label = member['label'] if member['label'] != 'Agent' else 'Agent' + (' ' + str(slot['key']) if slot else '')
                agents.append({**member, 'label': label, 'key': slot.get('key'), 'avatar': avatar,
                               'assignment': assignment, 'needsYouIds': [n['id'] for n in pending],
                               'resultIds': [r['id'] for r in self.results.values() if r['agentId'] == member['id']]})
            needs = [{**n, 'sources': sorted(n['sources']), 'stale': source_stale} for n in self.needs.values()]
            for request in needs:
                if 'manager_ledger' in request['sources']:
                    job = self.jobs.get(request['jobId'])
                    request['stale'] = request['stale'] or job is None or job['updated'] is None or now - job['updated'] > self.stale_after
                if 'desktop_status' in request['sources']:
                    thread = next((t for t, m in self.members.items() if m['id'] == request['agentId']), None)
                    runtime = runtimes.get(thread)
                    flags = runtime.get('activeFlags') if isinstance(runtime, dict) else None
                    live_wait = (isinstance(runtime, dict) and runtime.get('type') == 'active'
                                 and isinstance(flags, list) and all(isinstance(f, str) for f in flags)
                                 and bool({'waitingOnApproval', 'waitingOnUserInput'} & set(flags)))
                    request['stale'] = request['stale'] or not live_wait
            results = [{**r, 'stale': source_stale} for r in self.results.values()]
            # A cached completion can outlive its ledger row; recheck previously
            # emitted local links even during a source outage/compaction.
            for result in results:
                result['artifacts'] = copy.deepcopy(result['artifacts'])
                for artifact in result['artifacts']:
                    if artifact['kind'] == 'local' and artifact['href']:
                        path = Path(unquote(urlsplit(artifact['href']).path))
                        refreshed = self._artifacts({'artifacts': [{'path': str(path), 'public': True}]}, result['jobId'], [])
                        artifact.update({k: refreshed[0][k] for k in ('href', 'status')})
            return copy.deepcopy({
                'schemaVersion': 1, 'observedAt': _iso(now),
                'source': {'available': not issues and bool(self.members) and self.have_ledger,
                           'stale': source_stale, 'updatedAt': _iso(self.ledger_at), 'issues': issues},
                'agents': sorted(agents, key=lambda a: (a['key'] is None, a['key'] or 0, a['id'])),
                'needsYou': sorted(needs, key=lambda n: (n['firstObservedAt'], n['id'])),
                'results': sorted(results, key=lambda r: (r['completedAt'] or '', r['id']), reverse=True),
            })
