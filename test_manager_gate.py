import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from manager_gate import decide, observation, private_json, save_state, DesktopRequest, LocalManagerWatch


def wake_reply(owner='owner'):
    # Actual desktop IPC schema: the renderer's method wrapper is removed by
    # the bridge. In particular there is no result.method field.
    return {'type': 'response', 'resultType': 'success',
            'method': 'thread-follower-start-turn', 'handledByClientId': owner,
            'result': {'result': {'turn': {'id': 'new-turn', 'status': 'inProgress'}}}}


def observed(worker='idle', event='old', manager_idle=True):
    return {'managerIdle': manager_idle, 'workers': {'worker': {'state': worker, 'eventAt': event}}}


class PolicyTests(unittest.TestCase):
    def test_idle_overnight_never_requests_ai(self):
        state = {}
        for now in range(1000, 1000 + 12*3600, 15):
            state, status, wake = decide(state, observed(), now)
            self.assertFalse(wake)
            self.assertEqual(status, 'idle')

    def test_existing_completed_history_is_not_new_work(self):
        state, _, wake = decide({}, observed(), 1000)
        self.assertFalse(wake)
        self.assertNotIn('pendingAt', state)

    def test_new_worker_turn_wakes_after_settling(self):
        state, _, _ = decide({}, observed(), 1000)
        state, _, wake = decide(state, observed('working', 'new'), 1015)
        self.assertFalse(wake)
        state, _, wake = decide(state, observed('working', 'new'), 1045)
        self.assertTrue(wake)

    def test_fast_job_between_polls_is_not_missed(self):
        state, _, _ = decide({}, observed(), 1000)
        state, _, _ = decide(state, observed('idle', 'new-completion'), 1015)
        _, _, wake = decide(state, observed('idle', 'new-completion'), 1045)
        self.assertTrue(wake)

    def test_manager_busy_or_waiting_is_never_interrupted(self):
        state = {}
        for now in (1000, 1600, 5000):
            state, status, wake = decide(state, observed('working', manager_idle=False), now)
            self.assertFalse(wake)
            self.assertEqual(status, 'manager-busy')
        _, _, wake = decide(state, observed('working'), 5015)
        self.assertTrue(wake)

    def test_manager_activity_alone_does_not_trigger_itself(self):
        state, _, _ = decide({}, observed(manager_idle=False), 1000)
        for now in range(1015, 10000, 15):
            state, _, wake = decide(state, observed(manager_idle=now % 2 == 0), now)
            self.assertFalse(wake)

    def test_active_work_is_rate_limited_to_ten_minutes(self):
        state = {'baseline': observed('working')['workers'], 'lastWakeAt': 1000}
        for now in (1015, 1300, 1599):
            state, _, wake = decide(state, observed('working'), now)
            self.assertFalse(wake)
        self.assertTrue(decide(state, observed('working'), 1600)[2])

    def test_waiting_only_stays_quiet_after_one_report(self):
        state = {'baseline': observed('waiting')['workers'], 'lastWakeAt': 1000}
        for now in range(1015, 45000, 15):
            state, _, wake = decide(state, observed('waiting'), now)
            self.assertFalse(wake)

    def test_missing_activity_never_means_active(self):
        state = {'baseline': observed('working')['workers'], 'lastWakeAt': 0}
        self.assertEqual(decide(state, None, 5000)[1:], ('unavailable', False))

    def test_unknown_send_or_failure_never_auto_retries_even_after_restart(self):
        for field in ('dispatch', 'error'):
            state = {field: 'persisted', 'baseline': observed()['workers']}
            for now in (1000, 10000, 1000000):
                state, status, wake = decide(state, observed('working', 'new'), now)
                state = json.loads(json.dumps(state))
                self.assertEqual(status, 'paused-error')
                self.assertFalse(wake)

    def test_observation_requires_roster_and_authoritative_runtime(self):
        slots = [{'id': 'manager', 'state': 'idle', 'approvalStateAvailable': True},
                 {'id': 'worker', 'state': 'done', 'eventAt': 'today', 'approvalStateAvailable': True}]
        snap = {'connected': True, 'slots': slots}
        self.assertEqual(observation(snap, 'manager', ['worker']), observed(event='today'))
        self.assertIsNone(observation(snap, 'manager', ['missing']))
        slots[0]['state'] = 'waiting'
        self.assertFalse(observation(snap, 'manager', ['worker'])['managerIdle'])
        slots[1]['approvalStateAvailable'] = False
        self.assertIsNotNone(observation(snap, 'manager', ['worker']))
        slots[1]['eventAt'] = None
        self.assertIsNone(observation(snap, 'manager', ['worker']))
        self.assertIsNone(observation({'connected': False}, 'manager', ['worker']))

    def test_terminal_event_fallback_never_treats_quiet_work_as_idle(self):
        slots = [{'id': 'manager', 'state': 'idle', 'eventAt': 'complete'},
                 {'id': 'worker', 'state': 'working', 'eventAt': 'started'}]
        snap = {'connected': True, 'slots': slots}
        self.assertTrue(observation(snap, 'manager', ['worker'])['managerIdle'])
        slots[0]['state'] = 'unknown'
        self.assertFalse(observation(snap, 'manager', ['worker'])['managerIdle'])
        slots[0]['state'] = 'working'
        self.assertFalse(observation(snap, 'manager', ['worker'])['managerIdle'])
        slots[1]['state'] = 'unknown'
        self.assertIsNone(observation(snap, 'manager', ['worker']))

    def test_request_preserves_settings_and_only_targets_manager(self):
        client = DesktopRequest('/not-used')
        with patch.object(client, 'request', return_value=wake_reply()) as send:
            client.wake('manager', 'Bounded office check.', 'owner')
        args, kwargs = send.call_args
        self.assertEqual(args[0], 'thread-follower-start-turn')
        request = args[1]['turnStart']['request']
        self.assertEqual(set(request), {'threadId', 'input'})
        self.assertEqual(request['threadId'], 'manager')
        self.assertEqual(args[1]['turnStart']['context'], {'inheritThreadSettings': True})
        self.assertEqual(kwargs, {'version': 2, 'target': 'owner'})

    def test_wake_accepts_actual_desktop_response_envelope(self):
        client = DesktopRequest('/not-used')
        with patch.object(client, 'request', return_value=wake_reply()):
            self.assertTrue(client.wake('manager', 'One check.', 'owner'))

    def test_wake_rejects_wrong_owner_method_and_malformed_result(self):
        for changes in ({'handledByClientId': 'other'}, {'method': 'initialize'},
                        {'resultType': 'error'}, {'result': None}, {'result': []}):
            with self.subTest(changes=changes):
                client = DesktopRequest('/not-used')
                with patch.object(client, 'request', return_value={**wake_reply(), **changes}):
                    with self.assertRaises(RuntimeError):
                        client.wake('manager', 'One check.', 'owner')

    def test_real_frame_path_handles_fragmented_reply_and_unrelated_events(self):
        def frame(value):
            body = json.dumps(value).encode()
            return struct.pack('<I', len(body)) + body

        class Wire:
            def __init__(self): self.requests = []; self.buffer = bytearray()
            def settimeout(self, _seconds): pass
            def sendall(self, value):
                sent = json.loads(value[4:]); self.requests.append(sent)
                if sent['type'] != 'request': return
                reply = {**wake_reply(), 'requestId': sent['requestId']}
                self.buffer.extend(frame({'type': 'broadcast', 'method': 'client-status-changed'}))
                self.buffer.extend(frame({**reply, 'requestId': 'unrelated'}))
                self.buffer.extend(frame({'type': 'client-discovery-request', 'requestId': 'discovery'}))
                self.buffer.extend(frame(reply))
            def recv(self, size):
                size = min(size, 3)
                out = self.buffer[:size]; del self.buffer[:size]; return bytes(out)

        client = DesktopRequest('/not-used'); client.connection = wire = Wire()
        self.assertTrue(client.wake('manager', 'One check.', 'owner'))
        requests = [r for r in wire.requests if r['type'] == 'request']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['targetClientId'], 'owner')
        self.assertEqual(wire.requests[-1]['response'], {'canHandle': False})

    def test_public_watch_status_has_no_private_fields(self):
        watch = LocalManagerWatch(None, '/not-used')
        watch.publish({'baseline': {'private-id': 'secret'}, 'error': '/private/path', 'wakeCount': 2}, 'idle')
        self.assertEqual(set(watch.status()), {'enabled', 'status', 'wakeCount', 'lastLocalCheckAt', 'continuityStatus'})
        self.assertNotIn('private', json.dumps(watch.status()))


class StorageTests(unittest.TestCase):
    def test_one_time_live_verification_is_durable_and_does_not_become_idle_timer(self):
        self.run_watch_case(fail=False)

    def test_uncertain_send_is_persisted_and_not_retried(self):
        self.run_watch_case(fail=True)

    def run_watch_case(self, fail):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            roster = root / 'team.json'
            roster.write_text(json.dumps({'managerThreadId': 'manager', 'members': [
                {'threadId': 'manager', 'hostId': 'local'}, {'threadId': 'worker', 'hostId': 'local'}]}))
            config = root / 'manager-gate.json'
            save_state(config, {'enabled': True, 'verifyFirstWake': True,
                                'managerThreadId': 'manager', 'prompt': 'One check.'})
            snap = {'connected': True, 'slots': [
                {'id': 'manager', 'state': 'idle', 'eventAt': 'complete'},
                {'id': 'worker', 'state': 'idle', 'eventAt': 'complete'}]}
            office = SimpleNamespace(roster_path=roster, codex_dir=root, snapshot=lambda: snap)
            clock = [1000]
            class Stop:
                def __init__(self): self.polls = 0
                def is_set(self): return self.polls >= 80
                def wait(self, seconds): self.polls += 1; clock[0] += seconds
            with patch('manager_gate.time.time', side_effect=lambda: clock[0]), patch('manager_gate.DesktopRequest') as request:
                client = request.return_value.__enter__.return_value
                client.owner.return_value = 'owner'
                if fail: client.wake.side_effect = TimeoutError('unknown')
                for _ in range(2):
                    watch = LocalManagerWatch(office, config); watch.stop = Stop(); watch.run()
                self.assertEqual(client.wake.call_count, 1)
                state = private_json(root / 'manager-gate-state.json')
                if fail:
                    self.assertIn('dispatch', state); self.assertIn('error', state)
                    self.assertEqual(state['errorPhase'], 'wake-acknowledgement')
                    self.assertEqual(state['errorKind'], 'TimeoutError')
                    self.assertEqual(watch.status()['status'], 'paused-error')
                else:
                    self.assertTrue(state['verificationComplete']); self.assertEqual(state['wakeCount'], 1)
                    self.assertEqual(watch.status()['status'], 'idle')

    def test_state_is_private_and_recoverable_after_atomic_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            save_state(path, {'dispatch': {'outcome': 'pending'}})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn('dispatch', private_json(path))
            save_state(path, {'wakeCount': 1})
            self.assertEqual(private_json(path), {'wakeCount': 1})

    def test_insecure_file_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{}'); path.chmod(0o644)
            with self.assertRaises(ValueError): private_json(path)
            path.chmod(0o600)
            link = Path(folder) / 'link.json'; link.symlink_to(path)
            with self.assertRaises(ValueError): private_json(link)

    def test_corrupt_state_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text('{'); path.chmod(0o600)
            with self.assertRaises(ValueError): private_json(path)


if __name__ == '__main__':
    unittest.main()
