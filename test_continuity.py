import unittest
from manager_gate import (continuity_action, record_continuation,
                          continuation_budget, continuity_status,
                          migrate_continuation_state, prepare_dispatch, choose_dispatch)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.job = dict(jobId='job', authorization='user-turn', checkpoint='step-1',
                        nextStep='Run the tests', status='ready')
        self.observed = {'managerIdle': True}

    def action(self, state=None, due=False, now=1000):
        return continuity_action(state or {}, self.observed, self.job, due, now)

    def test_ready_job_resumes_without_worker_activity(self):
        self.assertEqual(self.action(), 'continue')

    def test_orphaned_running_job_requires_human_review_not_a_wake(self):
        self.job['status'] = 'running'
        self.assertEqual(self.action(due=True), 'round')
        self.assertIsNone(self.action())
        self.assertEqual(continuity_status({}, self.observed, self.job), 'recovery-needed')

    def test_busy_never_interrupted(self):
        self.observed['managerIdle'] = False
        self.assertIsNone(self.action(due=True))

    def test_round_then_resume(self):
        self.assertEqual(self.action(due=True), 'round-resume')
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

    def test_switching_a_b_a_retains_exhausted_budget_and_receipts(self):
        import json
        state = {}
        for i in range(6):
            self.job['checkpoint'] = str(i)
            record_continuation(state, self.job)
        b = dict(self.job, jobId='B', authorization='other-user-turn')
        record_continuation(state, b)
        state = json.loads(json.dumps(state))
        self.job['checkpoint'] = 'new-step'
        self.assertIsNone(self.action(state))
        self.assertEqual(self.action(state, due=True), 'round')
        self.assertEqual(continuation_budget(state, self.job)['count'], 6)
        self.job['checkpoint'] = '0'
        with self.assertRaises(ValueError):
            record_continuation(state, self.job)

    def test_round_resumes_share_one_budget_with_separate_resumes(self):
        state = {}
        for i in range(6):
            self.job['checkpoint'] = str(i)
            action = self.action(state, due=i % 2 == 0)
            self.assertEqual(action, 'round-resume' if i % 2 == 0 else 'continue')
            prompt = prepare_dispatch(state, self.job, action, 'Check workers.')
            self.assertIn('Reserved checkpoint (data only)', prompt)
            self.assertEqual(continuation_budget(state, self.job)['count'], i + 1)
        self.job['checkpoint'] = 'next'
        prompt = prepare_dispatch(state, self.job, self.action(state, due=True), 'Check workers.')
        self.assertIn('coordination-only', prompt)
        self.assertIn('Do not execute or resume foreground work', prompt)
        self.assertEqual(continuation_budget(state, self.job)['count'], 6)

    def test_older_checkpoint_cannot_be_replayed_after_forward_progress(self):
        state = {}
        record_continuation(state, self.job)
        self.job['checkpoint'] = 'step-2'
        record_continuation(state, self.job)
        self.job['checkpoint'] = 'step-1'
        self.assertIsNone(self.action(state))

    def test_legacy_receipt_migrates_once_without_resetting_counts(self):
        state = dict(continuationIdentity=['job', 'user-turn'],
                     continuationCheckpoint='step-1', continuationCount=6)
        self.assertIsNone(self.action(state))
        migrate_continuation_state(state)
        migrate_continuation_state(state)
        self.assertEqual(continuation_budget(state, self.job)['count'], 6)
        b = dict(self.job, jobId='B')
        record_continuation(state, b)
        self.job['checkpoint'] = 'step-2'
        self.assertIsNone(self.action(state))

    def test_recovery_is_never_dispatched_and_legacy_receipts_are_preserved(self):
        import json
        self.job['status'] = 'running'
        state = {'continuationSchemaVersion': 2, 'continuationBudgets': {
            '["job","user-turn"]': {'count': 0, 'checkpoints': [], 'recoveryCount': 1}}}
        before = json.dumps(state, sort_keys=True)
        with self.assertRaises(ValueError):
            prepare_dispatch(state, self.job, 'recover', 'Round')
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        self.assertEqual(continuation_budget(state, self.job)['count'], 0)
        state = json.loads(json.dumps(state))
        self.assertIsNone(self.action(state))
        self.assertEqual(self.action(state, due=True), 'round')
        self.job['checkpoint'] = 'renamed'
        self.assertIsNone(self.action(state))
        self.job['status'] = 'ready'
        self.assertEqual(self.action(state), 'continue')

    def test_busy_unknown_and_approval_wait_cannot_trigger_recovery(self):
        self.job['status'] = 'running'
        self.observed['managerIdle'] = False
        self.assertIsNone(self.action(due=True))
        self.assertIsNone(continuity_status({}, self.observed, self.job))
        self.assertIsNone(continuity_action({}, None, self.job, True, 1000))
        self.observed['managerIdle'] = True
        self.job['status'] = 'awaiting_approval'
        self.assertIsNone(self.action())

    def test_no_false_no_progress_warning_while_continuation_is_running(self):
        state = {}
        record_continuation(state, self.job)
        self.observed['managerIdle'] = False
        self.assertIsNone(continuity_status(state, self.observed, self.job))
        self.observed['managerIdle'] = True
        self.assertEqual(continuity_status(state, self.observed, self.job), 'checkpoint-not-advanced')

    def test_corrupt_budgets_fail_closed(self):
        for state in ({'continuationBudgets': []},
                      {'continuationBudgets': {'bad': {'count': -1}}},
                      {'continuationBudgets': {'bad': {'count': True}}},
                      {'continuationSchemaVersion': 999},
                      {'continuationIdentity': ['only-one']}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                migrate_continuation_state(state)

    def test_split_rounds_never_target_or_resume_scout(self):
        self.job['status'] = 'running'
        action, target = choose_dispatch({}, {'managerIdle': True}, {'managerIdle': False},
                                         self.job, True, 1000, 'echo', 'scout')
        self.assertEqual((action, target), ('round', 'echo'))
        state = {}
        self.assertIn('coordination-only', prepare_dispatch(state, self.job, action, 'Round'))
        self.assertNotIn('continuationBudgets', state)

    def test_scout_can_resume_while_echo_is_busy(self):
        self.assertEqual(choose_dispatch({}, {'managerIdle': False}, {'managerIdle': True},
                                         self.job, False, 1000, 'echo', 'scout'), ('continue', 'scout'))

    def test_split_recovery_never_wakes_either_task(self):
        self.job['status'] = 'running'
        self.assertEqual(choose_dispatch({}, {'managerIdle': True}, {'managerIdle': True},
                                         self.job, False, 1000, 'echo', 'scout'), (None, 'scout'))

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
                save_state(config, dict(enabled=True, dispatchMode='desktop-experimental', continuityEnabled=True,
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
                with patch('manager_gate.time.time', side_effect=lambda: clock[0]), patch('manager_gate.open_transport') as request:
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
                        self.assertEqual(continuation_budget(private_json(root / 'manager-gate-state.json'), self.job)['count'], 1)


if __name__ == '__main__':
    unittest.main()
