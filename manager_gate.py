"""Opt-in local manager gate. Idle polls never invoke a model.

The viewer remains read-only over HTTP. Only this explicitly enabled component
can request a turn, in one configured manager task. Uses an observed desktop IPC
protocol: unsupported versions and uncertain sends stop dispatch, not retry it.
"""
import copy
import fcntl
import json
import os
import socket
import stat
import struct
import threading
import time
import uuid
from pathlib import Path

MAX_FRAME = 32 * 1024 * 1024
MIN_ROUND_SECONDS = 600


def private_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Watch configuration/state must be an owner-only regular file')
    if info.st_size > 128 * 1024:
        raise ValueError('Watch file exceeds its size limit')
    return json.loads(path.read_text())


def save_state(path, state):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(state, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


class DesktopRequest:
    """Bounded same-user IPC client; never handles approvals or discoveries."""
    def __init__(self, path, timeout=30):
        self.path, self.timeout = Path(path), timeout

    def __enter__(self):
        info = self.path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Desktop socket must belong to the current user')
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(self.timeout)
        try:
            self.connection.connect(str(self.path))
            self.request('initialize', {'clientType': 'agent-office-local-watch'}, version=0)
        except Exception:
            self.connection.close()
            raise
        return self

    def __exit__(self, *_args):
        self.connection.close()

    def send(self, message):
        body = json.dumps(message).encode()
        self.connection.sendall(struct.pack('<I', len(body)) + body)

    def read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.connection.recv(size - len(data))
            if not part:
                raise EOFError('Desktop connection closed')
            data.extend(part)
        return data

    def request(self, method, params, version, target=None):
        rid = str(uuid.uuid4())
        message = {'type': 'request', 'requestId': rid, 'method': method,
                   'version': version, 'params': params}
        if target:
            message['targetClientId'] = target
        self.send(message)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.connection.settimeout(max(.01, deadline - time.monotonic()))
            size = struct.unpack('<I', self.read_exact(4))[0]
            if not 0 < size <= MAX_FRAME:
                raise ValueError('Unsupported desktop frame')
            result = json.loads(self.read_exact(size))
            if result.get('type') == 'client-discovery-request':
                self.send({'type': 'client-discovery-response', 'requestId': result['requestId'],
                           'response': {'canHandle': False}})
            if result.get('type') == 'response' and result.get('requestId') == rid:
                if result.get('resultType') != 'success' or result.get('method') != method:
                    raise RuntimeError('Desktop request was not accepted')
                return result
        raise TimeoutError('Desktop request outcome is unknown')

    def owner(self, manager_id):
        reply = self.request('thread-owner-discovery',
                             {'hostId': 'local', 'conversationId': manager_id}, version=1)
        owner = reply.get('handledByClientId')
        if not isinstance(owner, str) or not owner:
            raise RuntimeError('Manager task has no available desktop owner')
        return owner

    def wake(self, manager_id, prompt, owner):
        # No model, effort, permission, workspace, or approval overrides.
        reply = self.request('thread-follower-start-turn', {
            'conversationId': manager_id,
            'turnStart': {'request': {'threadId': manager_id,
                                     'input': [{'type': 'text', 'text': prompt, 'text_elements': []}]},
                          'context': {'inheritThreadSettings': True}},
        }, version=2, target=owner)
        # The desktop bridge unwraps the renderer's {method, result} before
        # returning it over IPC. The method belongs to the response envelope,
        # not reply.result. request() has already checked its request ID.
        if (reply.get('resultType') != 'success'
                or reply.get('method') != 'thread-follower-start-turn'
                or reply.get('handledByClientId') != owner
                or not isinstance(reply.get('result'), dict)):
            raise RuntimeError('Unexpected wake response; inspect the manager before retrying')
        return True


def observation(snapshot, manager_id, worker_ids):
    if not snapshot.get('connected'):
        return None
    slots = {s.get('id'): s for s in snapshot.get('slots', []) if s.get('id')}
    if not {manager_id, *worker_ids}.issubset(slots):
        return None  # Roster changes require deliberate reconfiguration.
    manager = slots[manager_id]
    # Positive idle evidence is required: live runtime status, or a persisted
    # terminal lifecycle event. Silence/age/unknown never establish idleness.
    available = (manager.get('approvalStateAvailable') or manager.get('eventAt')) and manager.get('state') in ('idle', 'done')
    workers = {}
    for wid in worker_ids:
        slot = slots[wid]
        if not (slot.get('approvalStateAvailable') or slot.get('eventAt')) or slot.get('state') in ('unknown', 'unassigned'):
            return None
        state = 'idle' if slot['state'] == 'done' else slot['state']
        workers[wid] = {'state': state, 'eventAt': slot.get('eventAt')}
    return {'managerIdle': bool(available), 'workers': workers}


def decide(previous, observed, now, interval=MIN_ROUND_SECONDS):
    """Pure policy, driven solely by whitelisted lifecycle/status metadata."""
    state = copy.deepcopy(previous)
    state['lastLocalCheckAt'] = now
    if state.get('dispatch') or state.get('error'):
        return state, 'paused-error', False
    if observed is None:
        return state, 'unavailable', False
    workers = observed['workers']
    active = any(w['state'] == 'working' for w in workers.values())
    if 'baseline' not in state:
        state['baseline'] = workers
        state['lastWakeAt'] = now - interval
        state['wakeCount'] = 0
        # Old idle history is a baseline, not new work.
        if active:
            state['pendingAt'] = now
    elif workers != state['baseline']:
        state['baseline'] = workers
        state['pendingAt'] = now
    if not observed['managerIdle']:
        return state, 'manager-busy', False
    if now - state.get('lastWakeAt', now) < interval:
        return state, 'watching' if active else 'idle', False
    pending = state.get('pendingAt')
    if pending is not None and now - pending < 30:
        return state, 'settling', False
    wake = pending is not None or active
    return state, 'ready' if wake else 'idle', wake


class LocalManagerWatch:
    def __init__(self, office, config_path):
        self.office = office
        self.config_path = Path(config_path)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.public = {'enabled': True, 'status': 'starting', 'wakeCount': 0}
        self.thread = threading.Thread(target=self.run, name='office-local-watch', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)

    def status(self):
        with self.lock:
            return dict(self.public)

    def publish(self, state, status):
        with self.lock:
            self.public = {'enabled': True, 'status': status,
                           'lastLocalCheckAt': state.get('lastLocalCheckAt'),
                           'wakeCount': state.get('wakeCount', 0)}

    def run(self):
        state, lock_file = {}, None
        try:
            config = private_json(self.config_path)
            if config.get('enabled') is not True:
                self.publish(state, 'disabled')
                return
            team = json.loads(self.office.roster_path.read_text())
            manager = team['managerThreadId']
            if config.get('managerThreadId') != manager:
                raise ValueError('Manager does not match the configured roster')
            ids = [m['threadId'] for m in team['members'] if m['threadId'] != manager]
            if len(ids) != len(set(ids)) or not 1 <= len(ids) <= 5 or any(m.get('hostId') != 'local' for m in team['members']):
                raise ValueError('Unsupported local roster')
            prompt = config['prompt']
            if not isinstance(prompt, str) or not 1 <= len(prompt) <= 12000:
                raise ValueError('Invalid manager prompt')
            state_path = self.config_path.with_name('manager-gate-state.json')
            lock_path = self.config_path.with_name('manager-gate.lock')
            fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            lock_file = os.fdopen(fd, 'w')
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = private_json(state_path) if state_path.exists() else {}
            if not isinstance(state, dict):
                raise ValueError('Invalid local watch state')
            while not self.stop.is_set():
                # Turning this private switch off takes effect without a restart.
                if private_json(self.config_path) != config:
                    self.publish(state, 'configuration-changed')
                    return
                observed = observation(self.office.snapshot(), manager, ids)
                state, status, wake = decide(state, observed, time.time())
                if config.get('verifyFirstWake') is True and not state.get('verificationComplete') and not state.get('dispatch') and not state.get('error'):
                    # Explicit one-time installation smoke test, never a timer.
                    state.setdefault('pendingAt', time.time())
                if wake:
                    phase = 'connect'
                    try:
                        with DesktopRequest(self.office.codex_dir / 'ipc' / 'ipc.sock') as client:
                            phase = 'owner-discovery'
                            owner = client.owner(manager)  # No AI request yet.
                            latest = observation(self.office.snapshot(), manager, ids)
                            if latest is None or not latest['managerIdle']:
                                self.publish(state, 'manager-busy')
                                self.stop.wait(15)
                                continue
                            # Persist BEFORE the send. A crash or timeout must not duplicate it.
                            state['dispatch'] = {'at': time.time(), 'outcome': 'pending'}
                            save_state(state_path, state)
                            phase = 'wake-acknowledgement'
                            client.wake(manager, prompt, owner)
                        state.pop('dispatch')
                        state.pop('pendingAt', None)
                        state['lastWakeAt'] = time.time()
                        state['wakeCount'] = state.get('wakeCount', 0) + 1
                        if config.get('verifyFirstWake') is True:
                            state['verificationComplete'] = True
                        status = 'requested'
                    except Exception as exc:
                        state['error'] = 'Wake failed or uncertain; review Scout before rearming the local watch.'
                        # Keep diagnostics useful without retaining raw responses,
                        # prompts, exception messages, or private app paths.
                        state['errorPhase'] = phase
                        state['errorKind'] = next((kind.__name__ for kind in (
                            TimeoutError, EOFError, ValueError, RuntimeError, OSError
                        ) if isinstance(exc, kind)), 'UnexpectedError')
                        status = 'paused-error'
                save_state(state_path, state)
                self.publish(state, status)
                self.stop.wait(15)
        except Exception:
            self.publish(state, 'paused-error')
        finally:
            if lock_file:
                lock_file.close()
