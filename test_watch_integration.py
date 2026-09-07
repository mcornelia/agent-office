"""Deterministic watch-loop tests. All desktop dispatches are mocked."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from manager_gate import (LocalManagerWatch, save_state, private_json,
                          continuation_budget, record_continuation)


class WatchIntegrationTests(unittest.TestCase):
    def exercise(self, *, split=False, phase='ready', worker='idle', scout='idle',
                 echo='idle', initial=None, fail=False, cancel=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            roster = root / 'team.json'
            manager = 'echo' if split else 'scout'
            members = ['scout', 'echo', 'worker']
            save_state(roster, {'managerThreadId': manager, 'members': [
                {'threadId': x, 'hostId': 'local'} for x in members]})
            config = dict(enabled=True, continuityEnabled=True, managerThreadId=manager, prompt='Inspect workers.')
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
                return target + '-owner'
            def wake(target, prompt, _owner):
                calls.append((target, prompt))
                pre_send.append(private_json(root / 'manager-gate-state.json'))
                if fail:
                    raise TimeoutError('Uncertain result')
            with patch('manager_gate.time.time', side_effect=lambda: clock[0]), patch('manager_gate.DesktopRequest') as request:
                client = request.return_value.__enter__.return_value
                client.owner.side_effect = owner
                client.wake.side_effect = wake
                for _ in range(2):
                    watch = LocalManagerWatch(office, root / 'manager-gate.json')
                    watch.stop = Stop()
                    watch.run()
            return job, private_json(root / 'manager-gate-state.json'), calls, pre_send, watch.status()

    def test_recovery_receipt_persisted_before_send_survives_restart(self):
        job, state, calls, receipts, public = self.exercise(phase='running')
        self.assertEqual(len(calls), 1)
        self.assertIn('read-only recovery', calls[0][1])
        self.assertEqual(receipts[0]['dispatch']['kind'], 'recover')
        self.assertEqual(continuation_budget(receipts[0], job)['recoveryCount'], 1)
        self.assertEqual(continuation_budget(state, job)['count'], 0)
        self.assertEqual(public['continuityStatus'], 'recovery-needed')

    def test_unknown_recovery_send_never_retried(self):
        _, state, calls, _, _ = self.exercise(phase='running', fail=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['dispatch']['kind'], 'recover')
        self.assertIn('error', state)

    def test_recovery_cancellation_rechecked_before_send(self):
        _, _, calls, _, _ = self.exercise(phase='running', cancel=True)
        self.assertEqual(calls, [])

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
