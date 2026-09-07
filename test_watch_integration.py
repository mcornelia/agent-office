"""Deterministic watch-loop tests. All desktop dispatches are mocked."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from manager_gate import (LocalManagerWatch, save_state, private_json, serialize_state, MAX_STATE_BYTES,
                          continuation_budget, record_continuation)


class WatchIntegrationTests(unittest.TestCase):
    def exercise(self, *, split=False, phase='ready', worker='idle', scout='idle',
                 echo='idle', initial=None, fail=False, cancel=False,
                 mode='desktop-experimental', change_mode=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            roster = root / 'team.json'
            manager = 'echo' if split else 'scout'
            members = ['scout', 'echo', 'worker']
            save_state(roster, {'managerThreadId': manager, 'members': [
                {'threadId': x, 'hostId': 'local'} for x in members]})
            config = dict(enabled=True, dispatchMode='desktop-experimental', continuityEnabled=True, managerThreadId=manager, prompt='Inspect workers.')
            if mode is None:
                config.pop('dispatchMode')
            else:
                config['dispatchMode'] = mode
            if split:
                config['foregroundThreadId'] = 'scout'
            save_state(root / 'manager-gate.json', config)
            job = dict(jobId='job', authorization='user-approval', checkpoint='one',
                       status=phase, nextStep='Verify results')
            ledger = root / ('foreground.json' if split else 'state.json')
            save_state(ledger, {'foreground': job})
            slots = [{'id': tid, 'state': status, 'eventAt': 'known-event'}
                     for tid, status in [('scout', scout), ('echo', echo), ('worker', worker)]]
            snap = {'connected': True, 'slots': slots}
            office = SimpleNamespace(roster_path=roster, codex_dir=root, snapshot=lambda: snap)
            if initial:
                save_state(root / 'manager-gate-state.json', initial)
            clock = [1000]
            calls, pre_send = [], []
            class Stop:
                def __init__(self): self.polls = 0
                def is_set(self): return self.polls >= 6
                def wait(self, seconds): self.polls += 1; clock[0] += seconds
            def owner(target):
                if cancel:
                    save_state(ledger, {'foreground': dict(job, status='canceled')})
                if change_mode:
                    save_state(root / 'manager-gate.json', {**config, 'dispatchMode': 'manual'})
                return target + '-owner'
            def wake(target, prompt, _owner):
                calls.append((target, prompt))
                pre_send.append(private_json(root / 'manager-gate-state.json'))
                if fail:
                    raise TimeoutError('Uncertain result')
            with patch('manager_gate.time.time', side_effect=lambda: clock[0]), patch('manager_gate.open_transport') as request:
                client = request.return_value.__enter__.return_value
                client.owner.side_effect = owner
                client.wake.side_effect = wake
                for _ in range(2):
                    watch = LocalManagerWatch(office, root / 'manager-gate.json')
                    watch.stop = Stop()
                    watch.run()
                if mode in (None, 'manual'):
                    request.assert_not_called()
            return job, private_json(root / 'manager-gate-state.json'), calls, pre_send, watch.status()

    def test_recovery_never_wakes_or_reserves_even_after_restart(self):
        job, state, calls, receipts, public = self.exercise(phase='running')
        self.assertEqual(calls, [])
        self.assertEqual(receipts, [])
        self.assertEqual(continuation_budget(state, job)['recoveryCount'], 0)
        self.assertEqual(continuation_budget(state, job)['count'], 0)
        self.assertEqual(public['continuityStatus'], 'recovery-needed')

    def test_pre_upgrade_uncertain_recovery_receipt_is_never_retried(self):
        initial = {'dispatch': {'kind': 'recover', 'outcome': 'pending'}, 'error': 'uncertain'}
        _, state, calls, _, _ = self.exercise(phase='running', initial=initial)
        self.assertEqual(calls, [])
        self.assertEqual(state['dispatch']['kind'], 'recover')
        self.assertIn('error', state)

    def test_split_orphaned_job_waits_for_user_but_coordinator_can_still_check(self):
        job, state, calls, _, public = self.exercise(split=True, phase='running', worker='working')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'echo')
        self.assertIn('coordination-only', calls[0][1])
        self.assertEqual(continuation_budget(state, job)['recoveryCount'], 0)
        self.assertEqual(public['continuityStatus'], 'recovery-needed')

    def test_manual_default_and_explicit_mode_never_connect_or_reserve(self):
        for mode in (None, 'manual'):
            with self.subTest(mode=mode), patch('desktop_dispatch.socket.socket') as socket:
                job, state, calls, receipts, public = self.exercise(mode=mode, worker='working')
                socket.assert_not_called()
                self.assertEqual(calls, [])
                self.assertEqual(receipts, [])
                self.assertEqual(continuation_budget(state, job)['count'], 0)
                self.assertEqual(public['status'], 'manual-required')

    def test_one_new_receipt_crossing_size_limit_preserves_original_and_never_wakes(self):
        key = '["old-job","old-approval"]'
        initial = {'continuationSchemaVersion': 2, 'continuationBudgets': {
            key: {'count': 1, 'checkpoints': [''], 'recoveryCount': 0}},
            'baseline': {'echo': {'state': 'idle', 'eventAt': 'known-event'},
                         'worker': {'state': 'idle', 'eventAt': 'known-event'}},
            'lastWakeAt': 0, 'wakeCount': 1, 'lastLocalCheckAt': 1000, 'continuityStatus': None}
        initial['continuationBudgets'][key]['checkpoints'][0] = 'x' * (MAX_STATE_BYTES - 80 - len(serialize_state(initial)))
        job, state, calls, receipts, public = self.exercise(initial=initial)
        self.assertEqual(calls, [])
        self.assertEqual(receipts, [])
        self.assertEqual(state, initial)
        self.assertEqual(continuation_budget(state, job)['count'], 0)
        self.assertEqual(public['status'], 'state-limit')

    def test_mode_change_during_owner_lookup_cancels_dispatch(self):
        job, state, calls, receipts, _ = self.exercise(change_mode=True)
        self.assertEqual(calls, [])
        self.assertEqual(receipts, [])
        self.assertEqual(continuation_budget(state, job)['count'], 0)

    def test_uncertain_send_at_capacity_keeps_pending_receipt_and_never_retries(self):
        key = '["old-job","old-approval"]'
        initial = {'continuationSchemaVersion': 2, 'continuationBudgets': {
            key: {'count': 1, 'checkpoints': [''], 'recoveryCount': 0}},
            'baseline': {'echo': {'state': 'idle', 'eventAt': 'known-event'},
                         'worker': {'state': 'idle', 'eventAt': 'known-event'}},
            'lastWakeAt': 0, 'wakeCount': 1, 'lastLocalCheckAt': 1000, 'continuityStatus': None}
        initial['continuationBudgets'][key]['checkpoints'][0] = 'x' * (MAX_STATE_BYTES - 260 - len(serialize_state(initial)))
        job, state, calls, receipts, public = self.exercise(initial=initial, fail=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['dispatch']['kind'], 'continue')
        self.assertEqual(state['dispatch']['outcome'], 'pending')
        self.assertEqual(continuation_budget(state, job)['count'], 1)
        self.assertEqual(state['continuationBudgets'][key], initial['continuationBudgets'][key])
        self.assertEqual(public['status'], 'paused-error')

    def test_combined_round_reserves_budget_before_send(self):
        initial = {'baseline': {'echo': {'state': 'idle', 'eventAt': 'known-event'},
                                'worker': {'state': 'working', 'eventAt': 'known-event'}},
                   'lastWakeAt': 0}
        job, state, calls, receipts, _ = self.exercise(worker='working', initial=initial)
        self.assertEqual(len(calls), 1)
        self.assertEqual(receipts[0]['dispatch']['kind'], 'round-resume')
        self.assertEqual(continuation_budget(receipts[0], job)['count'], 1)
        self.assertEqual(continuation_budget(state, job)['count'], 1)
        self.assertIn('one continuation', calls[0][1])

    def test_exhausted_round_is_coordination_only(self):
        state = {'baseline': {'echo': {'state': 'idle', 'eventAt': 'known-event'},
                              'worker': {'state': 'working', 'eventAt': 'known-event'}}, 'lastWakeAt': 0}
        job = dict(jobId='job', authorization='user-approval', status='ready', nextStep='Verify')
        for i in range(6):
            record_continuation(state, dict(job, checkpoint=str(i)))
        job, final, calls, _, _ = self.exercise(worker='working', initial=state)
        self.assertEqual(len(calls), 1)
        self.assertIn('coordination-only', calls[0][1])
        self.assertEqual(continuation_budget(final, job)['count'], 6)

    def test_echo_rounds_while_scout_working_no_execution_reservation(self):
        job, state, calls, _, _ = self.exercise(split=True, phase='running', scout='working')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'echo')
        self.assertIn('coordination-only', calls[0][1])
        self.assertEqual(continuation_budget(state, job)['count'], 0)

    def test_scout_resumes_even_if_echo_busy(self):
        _, _, calls, _, _ = self.exercise(split=True, echo='working')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'scout')
        self.assertIn('bounded continuation', calls[0][1])

    def test_split_idle_office_has_no_wakes(self):
        _, _, calls, _, _ = self.exercise(split=True, phase='completed')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
