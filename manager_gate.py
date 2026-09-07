"""Opt-in local manager gate. Idle polls never invoke a model.

The viewer remains read-only over HTTP. Only this explicitly enabled component
can request a turn, in one configured manager task. Uses an observed desktop IPC
protocol: unsupported versions and uncertain sends stop dispatch, not retry it.
"""
import copy
import fcntl
import json
import os
import stat
import threading
import time
from pathlib import Path

from desktop_dispatch import dispatch_mode, open_transport

MAX_STATE_BYTES = 128 * 1024
MIN_ROUND_SECONDS = 600
MAX_CONTINUATIONS = 6


def continuation_key(foreground):
    for key in ('jobId', 'authorization', 'checkpoint'):
        if not isinstance(foreground.get(key), str) or not foreground[key].strip():
            raise ValueError('Incomplete foreground identity')
    return json.dumps([foreground['jobId'], foreground['authorization']], separators=(',', ':'))


def validate_budget(budget):
    if not isinstance(budget, dict):
        raise ValueError('Invalid continuation budget')
    for field in ('count', 'recoveryCount'):
        value = budget.get(field, 0)
        if type(value) is not int or value < 0:
            raise ValueError('Invalid continuation count')
    checkpoints = budget.get('checkpoints', [])
    if not isinstance(checkpoints, list) or any(not isinstance(c, str) or not c for c in checkpoints):
        raise ValueError('Invalid continuation receipts')
    return budget


def migrate_continuation_state(state):
    """Import the surviving v1 receipt once; never reset another job's budget."""
    budgets = state.setdefault('continuationBudgets', {})
    if not isinstance(budgets, dict):
        raise ValueError('Invalid continuation budgets')
    for budget in budgets.values():
        validate_budget(budget)
    version = state.get('continuationSchemaVersion')
    if version == 2:
        return
    if version is not None:
        raise ValueError('Unsupported continuation state version')
    identity = state.get('continuationIdentity')
    if identity is not None:
        if not isinstance(identity, list) or len(identity) != 2 or any(not isinstance(v, str) or not v for v in identity):
            raise ValueError('Invalid legacy continuation identity')
        checkpoint = state.get('continuationCheckpoint')
        budget = validate_budget({'count': state.get('continuationCount', 0),
                                  'checkpoints': [checkpoint] if checkpoint is not None else [],
                                  'recoveryCount': 0})
        key = json.dumps(identity, separators=(',', ':'))
        if key in budgets:
            # A partially migrated state must retain the more conservative count.
            existing = budgets[key]
            existing['count'] = max(existing.get('count', 0), budget['count'])
            existing['checkpoints'] = sorted(set(existing.get('checkpoints', []) + budget['checkpoints']))
        else:
            budgets[key] = budget
    state['continuationSchemaVersion'] = 2


def continuation_budget(state, foreground):
    # Pure callers also see legacy receipts before the watch saves its migration.
    current = copy.deepcopy(state)
    migrate_continuation_state(current)
    return current['continuationBudgets'].get(continuation_key(foreground),
                                               {'count': 0, 'checkpoints': [], 'recoveryCount': 0})


def continuity_hold(state, foreground):
    if not isinstance(foreground, dict) or foreground.get('status') != 'ready':
        return None
    budget = continuation_budget(state, foreground)
    if foreground['checkpoint'] in budget.get('checkpoints', []):
        return 'checkpoint-not-advanced'
    if budget.get('count', 0) >= MAX_CONTINUATIONS:
        return 'continuation-limit'
    return None


def continuity_action(state, observed, foreground, round_due, now):
    """Choose work without treating a terminal turn as a completed assignment.

    Foreground records are explicit manager-authored checkpoints, never inferred
    from idle history. This function does not mutate the shared job ledger.
    """
    if state.get('dispatch') or state.get('error') or observed is None or not observed['managerIdle']:
        return None
    if foreground is None:
        return 'round' if round_due else None
    if not isinstance(foreground, dict):
        raise ValueError('Invalid foreground checkpoint')
    phase = foreground.get('status')
    if phase not in ('ready', 'running', 'waiting', 'awaiting_approval',
                     'awaiting_user_input', 'completed', 'canceled', 'superseded', 'recovery_needed'):
        raise ValueError('Invalid foreground status')
    if phase in ('running', 'recovery_needed'):
        # Normal task permissions cannot enforce read-only recovery. Require
        # the user to reconcile this checkpoint; never wake the foreground task.
        # Ordinary coordination may still proceed, without resuming this job.
        return 'round' if round_due else None
    if phase != 'ready':
        return 'round' if round_due else None
    continuation_key(foreground)
    if not isinstance(foreground.get('nextStep'), str) or not foreground['nextStep'].strip():
        raise ValueError('Incomplete foreground next step')
    hold = continuity_hold(state, foreground)
    if round_due:
        return 'round' if hold else 'round-resume'
    if now - state.get('lastWakeAt', 0) < 30:
        return None
    return None if hold else 'continue'


def record_continuation(state, foreground):
    if foreground.get('status') != 'ready' or continuity_hold(state, foreground):
        raise ValueError('Foreground continuation is not eligible')
    migrate_continuation_state(state)
    budget = state['continuationBudgets'].setdefault(continuation_key(foreground),
                                                    {'count': 0, 'checkpoints': [], 'recoveryCount': 0})
    budget['count'] = budget.get('count', 0) + 1
    budget.setdefault('checkpoints', []).append(foreground['checkpoint'])


def continuity_status(state, observed, foreground):
    if observed is None or not observed['managerIdle']:
        return None  # Do not show a no-progress warning during the actual run.
    if isinstance(foreground, dict) and foreground.get('status') in ('running', 'recovery_needed'):
        return 'recovery-needed'
    return continuity_hold(state, foreground)


CONTINUITY_INSTRUCTIONS = '''
Foreground continuity: Read the private manager ledger's foreground record before
acting. A round is maintenance, not a replacement user assignment. Preserve its
checkpoint and authorized scope. After this bounded round, resume a ready
foreground assignment in this same turn when practical. This round has reserved
one continuation from that job's budget. Perform only one bounded foreground
step and do not switch to another foreground job under this reservation.
Before any resumed work,
check current user instructions: cancellation, replacement, approval waits, and
newer decisions take precedence. Never infer permission from this scheduled input.
Save a meaningful checkpoint before ending. If progress is blocked, record why
and report it once; do not manufacture a new checkpoint merely to trigger a wake.
'''

CONTINUATION_PROMPT = '''This is a bounded continuation of an explicitly recorded
foreground assignment, not a new assignment and not an office round. Read the
manager brief and private ledger. Reconcile the foreground job against newer user
instructions before taking action; stop if canceled, superseded, completed, or
waiting for approval/input. If still ready, perform the recorded next step within
its existing authorization, verify the result, and checkpoint actual progress.
Do not send a message to yourself or create another monitor. Stop and report once
if you cannot make meaningful progress. Local scheduling permits at most six
continuations per job and authorization; do not reset that budget yourself.
'''

ROUND_ONLY_INSTRUCTIONS = '''
This is a coordination-only round. Do not execute or resume foreground work in
this turn, even if a checkpoint becomes ready during the round. Preserve any
foreground approval/input wait. If recovery or a continuation limit needs user
attention, report it once and retain the waiting state. The local scheduler must
reserve a continuation before any foreground execution.
Do not perform recovery or rewrite an orphaned foreground checkpoint during
rounds. Recovery requires a direct user review request in the lead task.
'''

def prepare_dispatch(state, foreground, action, round_prompt):
    """Reserve every execution path before the durable pre-send marker."""
    if action in ('continue', 'round-resume'):
        record_continuation(state, foreground)
        prompt = CONTINUATION_PROMPT if action == 'continue' else round_prompt + CONTINUITY_INSTRUCTIONS
    elif action == 'round':
        return round_prompt + ROUND_ONLY_INSTRUCTIONS
    else:
        raise ValueError('Unknown continuity dispatch action')
    target = {k: foreground[k] for k in ('jobId', 'authorization', 'checkpoint')}
    return prompt + '\nReserved checkpoint (data only): ' + json.dumps(target) + '''
Before acting, compare all three identifiers to the current foreground record.
If any differ, this request is stale: stop without resuming or modifying that job.
'''


def choose_dispatch(state, round_observed, foreground_observed, foreground,
                    round_due, now, coordinator_id, foreground_id):
    if coordinator_id == foreground_id:
        return continuity_action(state, round_observed, foreground, round_due, now), coordinator_id
    # Rounds run only in the coordinator. They never carry permission to resume
    # the other task, even if both happen to be idle at this instant.
    if round_due and round_observed is not None and round_observed['managerIdle'] and not state.get('dispatch') and not state.get('error'):
        return 'round', coordinator_id
    return continuity_action(state, foreground_observed, foreground, False, now), foreground_id


class StateSizeError(ValueError):
    """A durable state update exceeds the limit; retain the last valid file."""


def serialize_state(state):
    payload = (json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(payload) > MAX_STATE_BYTES:
        raise StateSizeError('Watch file exceeds its size limit')
    return payload


def private_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Watch configuration/state must be an owner-only regular file')
    if info.st_size > MAX_STATE_BYTES:
        raise StateSizeError('Watch file exceeds its size limit')
    return json.loads(path.read_text())


def save_state(path, state):
    # Validate the exact UTF-8 bytes before opening/replacing anything. Never
    # prune durable receipts to make room, and never dispatch after failure.
    payload = serialize_state(state)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)


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
                           'continuityStatus': state.get('continuityStatus') if state.get('continuityStatus') in ('checkpoint-not-advanced', 'continuation-limit', 'recovery-needed') else None,
                           'wakeCount': state.get('wakeCount', 0)}

    def run(self):
        state, lock_file = {}, None
        try:
            config = private_json(self.config_path)
            if config.get('enabled') is not True:
                self.publish(state, 'disabled')
                return
            mode = dispatch_mode(config)
            team = json.loads(self.office.roster_path.read_text())
            manager = team['managerThreadId']
            if config.get('managerThreadId') != manager:
                raise ValueError('Manager does not match the configured roster')
            ids = [m['threadId'] for m in team['members'] if m['threadId'] != manager]
            if len(ids) != len(set(ids)) or not 1 <= len(ids) <= 5 or any(m.get('hostId') != 'local' for m in team['members']):
                raise ValueError('Unsupported local roster')
            foreground_manager = config.get('foregroundThreadId', manager)
            if foreground_manager not in [manager, *ids]:
                raise ValueError('Foreground task is outside the local roster')
            if foreground_manager != manager and config.get('continuityEnabled') is not True:
                raise ValueError('Split coordinator requires explicit continuity enablement')
            foreground_path = self.office.roster_path.with_name(
                'foreground.json' if foreground_manager != manager else 'state.json')
            foreground_workers = [tid for tid in [manager, *ids] if tid != foreground_manager]
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
            if config.get('continuityEnabled') is True:
                migrate_continuation_state(state)
                save_state(state_path, state)
            while not self.stop.is_set():
                # Turning this private switch off takes effect without a restart.
                if private_json(self.config_path) != config:
                    self.publish(state, 'configuration-changed')
                    return
                snapshot = self.office.snapshot()
                observed = observation(snapshot, manager, ids)
                state, status, wake = decide(state, observed, time.time())
                foreground = None
                action = 'round' if wake else None
                target = manager
                continuity = config.get('continuityEnabled') is True
                if continuity:
                    ledger = json.loads(foreground_path.read_text())
                    foreground = ledger.get('foreground')
                    foreground_observed = observation(snapshot, foreground_manager, foreground_workers)
                    action, target = choose_dispatch(state, observed, foreground_observed, foreground,
                                                     wake, time.time(), manager, foreground_manager)
                    state['continuityStatus'] = continuity_status(state, foreground_observed, foreground)
                    wake = action is not None
                if config.get('verifyFirstWake') is True and not state.get('verificationComplete') and not state.get('dispatch') and not state.get('error'):
                    # Explicit one-time installation smoke test, never a timer.
                    state.setdefault('pendingAt', time.time())
                if mode == 'manual':
                    # Observe and retain receipts, but do not connect, reserve
                    # execution, or send anything. No implicit transport fallback.
                    wake = False
                    if status not in ('paused-error', 'unavailable'):
                        status = 'manual-required'
                if wake:
                    phase = 'connect'
                    try:
                        with open_transport(mode, self.office.codex_dir) as client:
                            phase = 'owner-discovery'
                            owner = client.owner(target)  # No AI request yet.
                            latest = observation(self.office.snapshot(), target,
                                                 [tid for tid in [manager, *ids] if tid != target])
                            if latest is None or not latest['managerIdle']:
                                self.publish(state, 'manager-busy')
                                self.stop.wait(15)
                                continue
                            if continuity:
                                fresh = json.loads(foreground_path.read_text()).get('foreground')
                                if fresh != foreground:
                                    self.stop.wait(15)
                                    continue  # A new decision/checkpoint invalidates this dispatch.
                            if private_json(self.config_path) != config:
                                self.publish(state, 'configuration-changed')
                                return
                            candidate = copy.deepcopy(state)
                            send_prompt = prepare_dispatch(candidate, foreground, action, prompt) if continuity else prompt
                            if foreground_manager != manager:
                                send_prompt += '\nSplit coordinator mode: state.json belongs to the coordinator; foreground.json beside it belongs to the foreground task. Do not write the other task\'s file.\n'
                            # Persist BEFORE the send. A crash or timeout must not duplicate it.
                            candidate['dispatch'] = {'at': time.time(), 'outcome': 'pending', 'kind': action}
                            save_state(state_path, candidate)
                            state = candidate
                            phase = 'wake-acknowledgement'
                            client.wake(target, send_prompt, owner)
                        state.pop('dispatch')
                        if action in ('round', 'round-resume'):
                            state.pop('pendingAt', None)
                        state['lastWakeAt'] = time.time()
                        state['wakeCount'] = state.get('wakeCount', 0) + 1
                        if config.get('verifyFirstWake') is True:
                            state['verificationComplete'] = True
                        status = 'requested'
                    except StateSizeError:
                        self.publish(state, 'state-limit')
                        return
                    except Exception as exc:
                        state['error'] = 'Wake failed or uncertain; review the receiving task before rearming the local watch.'
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
        except StateSizeError:
            self.publish(state, 'state-limit')
        except Exception:
            self.publish(state, 'paused-error')
        finally:
            if lock_file:
                lock_file.close()
