import unittest
from manager_gate import continuity_action, record_continuation


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.job = dict(jobId='job', authorization='user-turn', checkpoint='step-1',
                        nextStep='Run the tests', status='ready')
        self.observed = {'managerIdle': True}

    def action(self, state=None, due=False, now=1000):
        return continuity_action(state or {}, self.observed, self.job, due, now)

    def test_ready_job_resumes_without_worker_activity(self):
        self.assertEqual(self.action(), 'continue')

    def test_running_is_not_a_safe_checkpoint(self):
        self.job['status'] = 'running'
        self.assertIsNone(self.action(due=True))

    def test_busy_never_interrupted(self):
        self.observed['managerIdle'] = False
        self.assertIsNone(self.action(due=True))

    def test_round_then_resume(self):
        self.assertEqual(self.action(due=True), 'round')
        self.assertIsNone(self.action({'lastWakeAt': 990}))
        self.assertEqual(self.action({'lastWakeAt': 960}), 'continue')

    def test_stopped_or_waiting_never_resumes(self):
        for phase in ('waiting', 'awaiting_approval', 'awaiting_user_input',
                      'completed', 'canceled', 'superseded'):
            self.job['status'] = phase
            self.assertIsNone(self.action())
            self.assertEqual(self.action(due=True), 'round')

    def test_checkpoint_deduplicated_even_after_restart(self):
        import json
        state = {}
        record_continuation(state, self.job)
        state = json.loads(json.dumps(state))
        self.assertIsNone(self.action(state))
        self.job['checkpoint'] = 'step-2'
        self.assertEqual(self.action(state), 'continue')

    def test_budget_cannot_be_reset_by_new_checkpoint(self):
        state = {}
        for i in range(6):
            self.job['checkpoint'] = str(i)
            self.assertEqual(self.action(state), 'continue')
            record_continuation(state, self.job)
        self.job['checkpoint'] = 'another'
        self.assertIsNone(self.action(state))

    def test_uncertain_send_never_retried(self):
        for state in ({'dispatch': {'outcome': 'pending'}}, {'error': 'unknown'}):
            self.assertIsNone(self.action(state, due=True))

    def test_idle_night_has_no_continuations(self):
        for now in range(0, 43200, 15):
            self.assertIsNone(continuity_action({}, self.observed, None, False, now))

    def test_invalid_record_fails_closed(self):
        self.job.pop('authorization')
        with self.assertRaises(ValueError):
            self.action()

    def test_real_watch_resumes_once_and_rechecks_cancellation(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from manager_gate import LocalManagerWatch, save_state, private_json
        for cancel in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                roster = root / 'team.json'
                roster.write_text(json.dumps({'managerThreadId': 'manager', 'members': [
                    {'threadId': 'manager', 'hostId': 'local'},
                    {'threadId': 'worker', 'hostId': 'local'}]}))
                ledger = root / 'state.json'
                save_state(ledger, {'foreground': self.job})
                config = root / 'manager-gate.json'
                save_state(config, dict(enabled=True, continuityEnabled=True,
                                        managerThreadId='manager', prompt='Round'))
                snap = {'connected': True, 'slots': [
                    {'id': 'manager', 'state': 'idle', 'eventAt': 'done'},
                    {'id': 'worker', 'state': 'idle', 'eventAt': 'done'}]}
                office = SimpleNamespace(roster_path=roster, codex_dir=root, snapshot=lambda: snap)
                clock = [1000]
                class Stop:
                    def __init__(self): self.polls = 0
                    def is_set(self): return self.polls >= 5
                    def wait(self, seconds): self.polls += 1; clock[0] += seconds
                with patch('manager_gate.time.time', side_effect=lambda: clock[0]), patch('manager_gate.DesktopRequest') as request:
                    client = request.return_value.__enter__.return_value
                    def owner(_):
                        if cancel:
                            save_state(ledger, {'foreground': dict(self.job, status='canceled')})
                        return 'owner'
                    client.owner.side_effect = owner
                    for _ in range(2):  # Restart must retain the receipt.
                        watch = LocalManagerWatch(office, config)
                        watch.stop = Stop()
                        watch.run()
                    self.assertEqual(client.wake.call_count, 0 if cancel else 1)
                    if not cancel:
                        self.assertIn('bounded continuation', client.wake.call_args.args[1])
                        self.assertEqual(private_json(root / 'manager-gate-state.json')['continuationCount'], 1)


if __name__ == '__main__':
    unittest.main()
