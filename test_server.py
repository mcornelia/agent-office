import json
import io
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from server import EventTail, OfficeState, is_allowed_request, make_handler, presentation_snapshot


def event(kind):
    return json.dumps({"timestamp":"2026-09-05T21:30:00Z","type":"event_msg","payload":{"type":kind}}).encode() + b"\n"


class OfficeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'sessions').mkdir()
        self.app = {"pinned-thread-ids":[],"electron-persisted-atom-state":{"unread-thread-ids-by-host-v1":{"local":[]}}}
        self.write_app()
        self.db = self.root / 'state_5.sqlite'
        with sqlite3.connect(self.db) as c:
            c.executescript("CREATE TABLE thread_sections(id TEXT,name TEXT); CREATE TABLE threads(id TEXT,title TEXT,rollout_path TEXT,thread_section_id TEXT,archived INTEGER,section_position INTEGER); INSERT INTO thread_sections VALUES('pins','Pinned');")

    def write_app(self):
        (self.root / '.codex-global-state.json').write_text(json.dumps(self.app))

    def add_task(self, name, position, kind='task_started'):
        path = self.root / 'sessions' / (name+'.jsonl')
        path.write_bytes(event(kind))
        with sqlite3.connect(self.db) as c:
            c.execute('INSERT INTO threads VALUES(?,?,?,?,0,?)',(name,name,str(path),'pins',position))
        return path

    def test_partial_event_is_not_lost(self):
        path = self.add_task('one',100)
        tail = EventTail(path)
        self.assertEqual(tail.read()[0],'working')
        complete = event('task_complete')
        with path.open('ab') as f: f.write(complete[:25])
        self.assertEqual(tail.read()[0],'working')
        with path.open('ab') as f: f.write(complete[25:])
        self.assertEqual(tail.read()[0],'complete')

    def test_proxy_origin_policy_separates_https_public_from_http_loopback(self):
        public = ['glyph.local:4318']
        self.assertTrue(is_allowed_request('glyph.local:4318', 'https://glyph.local:4318', 4319, public))
        self.assertFalse(is_allowed_request('glyph.local:4318', 'http://glyph.local:4318', 4319, public))
        self.assertFalse(is_allowed_request('evil.example:4318', 'https://evil.example:4318', 4319, public))
        self.assertFalse(is_allowed_request('glyph.local:4318', 'https://evil.example:4318', 4319, public))
        self.assertTrue(is_allowed_request('127.0.0.1:4319', 'http://127.0.0.1:4319', 4319, public))
        self.assertTrue(is_allowed_request('localhost:4319', 'http://localhost:4319', 4319, public))
        self.assertFalse(is_allowed_request('127.0.0.1:4319', 'https://127.0.0.1:4319', 4319, public))

    def test_file_replacement_resets_lifecycle(self):
        path = self.add_task('one',100,'task_complete')
        tail = EventTail(path)
        self.assertEqual(tail.read()[0],'complete')
        replacement = path.with_suffix('.tmp')
        replacement.write_bytes(event('task_started'))
        replacement.replace(path)
        self.assertEqual(tail.read()[0],'working')

    def test_stale_work_is_unknown(self):
        path = self.add_task('one',100)
        os.utime(path,(time.time()-600,time.time()-600))
        self.assertEqual(EventTail(path).read()[0],'unknown')

    def test_pin_reorder_keeps_character_identity(self):
        self.add_task('one',100)
        self.add_task('two',200)
        state = OfficeState(self.root)
        first = state.snapshot()
        self.assertTrue(first['connected'])
        avatars = {s['id']:s['avatar'] for s in first['slots'] if s['id']}
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE threads SET section_position=50 WHERE id='two'")
        second = state.snapshot()
        self.assertEqual([s['id'] for s in second['slots'][:2]],['two','one'])
        self.assertEqual({s['id']:s['avatar'] for s in second['slots'] if s['id']},avatars)
        self.assertEqual(len({s['avatar'] for s in second['slots']}),6)

    def test_unread_completion_matches_green(self):
        self.add_task('one',100,'task_complete')
        state = OfficeState(self.root)
        self.assertEqual(state.snapshot()['slots'][0]['state'],'idle')
        self.app['electron-persisted-atom-state']['unread-thread-ids-by-host-v1']['local']=['one']
        self.write_app()
        self.assertEqual(state.snapshot()['slots'][0]['state'],'done')

    def test_current_task_name_wins_over_original_prompt_title(self):
        self.add_task('one',100)
        with sqlite3.connect(self.db) as c:
            c.execute('ALTER TABLE threads ADD COLUMN name TEXT')
            c.execute("UPDATE threads SET name='Renamed agent' WHERE id='one'")
        self.assertEqual(OfficeState(self.root).snapshot()['slots'][0]['title'],'Renamed agent')

    def test_missing_session_does_not_invent_activity(self):
        self.add_task('one',100).unlink()
        self.assertEqual(OfficeState(self.root).snapshot()['slots'][0]['state'],'unknown')

    def test_empty_pins_produce_six_empty_slots(self):
        snap = OfficeState(self.root).snapshot()
        self.assertTrue(snap['connected'])
        self.assertEqual(len(snap['slots']),6)
        self.assertEqual({s['state'] for s in snap['slots']},{'unassigned'})

    def test_no_transcript_fields_returned(self):
        path = self.add_task('one',100)
        with path.open('ab') as f:
            f.write(json.dumps({'type':'response_item','payload':{'text':'PRIVATE TEST TRANSCRIPT'}}).encode()+b'\n')
        self.assertNotIn('PRIVATE TEST TRANSCRIPT',json.dumps(OfficeState(self.root).snapshot()))

    def test_job_board_is_wired_and_presentation_route_omits_its_copy(self):
        self.add_task('one', 100, 'task_complete')
        roster = self.root / 'team.json'
        roster.write_text(json.dumps({
            'schemaVersion': 1,
            'managerThreadId': 'one',
            'members': [{'threadId': 'one', 'hostId': 'local',
                         'presentation': {'label': 'Scout'}}],
        }))
        (self.root / 'state.json').write_text(json.dumps({
            'schemaVersion': 1,
            'lastCheckAt': time.time(),
            'jobs': [{
                'taskId': 'one', 'sourceTurnId': 'safe-job',
                'lastObservedStatus': 'completed', 'completedAt': time.time(),
                'presentation': {'title': 'Safe result', 'summary': 'Safe summary'},
            }],
        }))
        snapshot = OfficeState(self.root, roster_path=roster).snapshot()
        self.assertEqual(snapshot['jobBoard']['agents'][0]['label'], 'Scout')
        self.assertEqual(snapshot['jobBoard']['results'][0]['title'], 'Safe result')
        public = presentation_snapshot(snapshot)
        self.assertEqual(public['jobBoard']['agents'], [])
        self.assertEqual(public['jobBoard']['results'], [])
        self.assertNotIn('Safe result', json.dumps(public))

    def test_presentation_snapshot_never_returns_private_assignments(self):
        private = {
            'connected': True,
            'source': 'private source description',
            'approvalStateAvailable': True,
            'observedAt': '2026-09-05T21:30:00+00:00',
            'slots': [
                {'key': 1, 'id': 'private-thread-id', 'title': 'Secret assignment',
                 'avatar': 4, 'state': 'working', 'eventAt': None,
                 'approvalStateAvailable': True},
                {'key': 2, 'id': None, 'title': 'No pinned task', 'avatar': 2,
                 'state': 'unassigned', 'eventAt': None,
                 'approvalStateAvailable': False},
            ],
            'communications': [{'from': 'private-thread-id', 'to': 'other-private-id'}],
        }
        public = presentation_snapshot(private)
        encoded = json.dumps(public)
        self.assertTrue(public['presentation'])
        self.assertEqual(public['slots'][0]['title'], 'Agent 1')
        self.assertEqual(public['slots'][0]['id'], 'presentation-slot-1')
        self.assertIsNone(public['slots'][1]['id'])
        self.assertEqual(public['communications'], [])
        for secret in ('private-thread-id', 'other-private-id', 'Secret assignment', 'private source description'):
            self.assertNotIn(secret, encoded)

    def test_presentation_error_does_not_return_local_details(self):
        public = presentation_snapshot({'connected': False, 'error': '/Users/private/.codex missing', 'slots': []})
        self.assertEqual(public['error'], 'Activity unavailable')
        self.assertNotIn('/Users/private', json.dumps(public))

    def test_presentation_query_reaches_redacted_snapshot(self):
        class State:
            presentation = None
            def snapshot(self, presentation=False):
                self.presentation = presentation
                return {'connected': False, 'presentation': presentation, 'slots': []}
        state = State()
        handler_type = make_handler(state, 4318)
        handler = handler_type.__new__(handler_type)
        handler.headers = {'Host': '127.0.0.1:4318'}
        handler.path = '/api/state?presentation=1'
        handler.wfile = io.BytesIO()
        handler.send_response = lambda _code: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.send_error = lambda code: self.fail(f'unexpected HTTP error {code}')
        handler.do_GET()
        self.assertTrue(state.presentation)
        self.assertTrue(json.loads(handler.wfile.getvalue())['presentation'])

    def test_health_endpoint_contains_no_task_state(self):
        class State:
            def snapshot(self, presentation=False):
                self.fail('health must not read task state')
        handler_type = make_handler(State(), 4318)
        handler = handler_type.__new__(handler_type)
        handler.headers = {'Host': 'localhost:4318'}
        handler.path = '/api/health'
        handler.wfile = io.BytesIO()
        handler.send_response = lambda _code: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.send_error = lambda code: self.fail(f'unexpected HTTP error {code}')
        handler.do_GET()
        health = json.loads(handler.wfile.getvalue())
        self.assertEqual(health['service'], 'agent-office')
        self.assertNotIn('slots', health)

    def test_waiting_overrides_working_and_clears_on_response(self):
        self.add_task('one',100)
        class Status:
            value={'type':'active','activeFlags':['waitingOnApproval']}
            def follow(self, ids): self.ids=set(ids)
            def get(self, thread_id): return self.value
        status=Status()
        state=OfficeState(self.root,desktop_status=status)
        self.assertEqual(state.snapshot()['slots'][0]['state'],'waiting')
        status.value={'type':'active','activeFlags':[]}
        self.assertEqual(state.snapshot()['slots'][0]['state'],'working')
        status.value={'type':'active','activeFlags':['waitingOnUserInput']}
        self.assertEqual(state.snapshot()['slots'][0]['state'],'waiting')
        status.value=None
        fallback=state.snapshot()['slots'][0]
        self.assertEqual(fallback['state'],'working')
        self.assertFalse(fallback['approvalStateAvailable'])

    def test_missing_desktop_runtime_preserves_only_fresh_event_state(self):
        path=self.add_task('one',100)
        class Status:
            def follow(self, ids): self.ids=set(ids)
            def get(self, thread_id): return None
        state=OfficeState(self.root,desktop_status=Status())
        fresh=state.snapshot()['slots'][0]
        self.assertEqual(fresh['state'],'working')
        self.assertFalse(fresh['approvalStateAvailable'])
        os.utime(path,(time.time()-600,time.time()-600))
        stale=OfficeState(self.root,desktop_status=Status()).snapshot()['slots'][0]
        self.assertEqual(stale['state'],'unknown')
        self.assertFalse(stale['approvalStateAvailable'])


if __name__ == '__main__':
    unittest.main()
