import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from server import EventTail, OfficeState


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
        self.assertEqual(state.snapshot()['slots'][0]['state'],'unknown')


if __name__ == '__main__':
    unittest.main()
