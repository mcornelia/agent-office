import copy
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from activity_state import ActivityCheckpoints, EventTail, MAX_CHECKPOINT_BYTES
import test_server as fixtures
from test_server import event


class ActivityReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.jsonl'
        self.path.write_bytes(event('task_started'))

    def append(self, body):
        with self.path.open('ab') as stream:
            stream.write(body)

    def read_to_end(self, reader):
        for _ in range(100):
            result = reader.read()
            if reader.offset == self.path.stat().st_size and not reader.pending:
                return result
        self.fail('Reader did not catch up within a bounded test')

    def checkpoint(self, kind='task_complete'):
        self.path.write_bytes(event(kind))
        reader = EventTail(self.path)
        reader.read()
        return copy.deepcopy(reader.record)

    def test_restart_retains_event_outside_tail_without_rescanning_history(self):
        with patch('activity_state.MAX_TAIL', 512):
            reader = EventTail(self.path)
            self.assertEqual(reader.read()[0], 'working')
            for _ in range(40):
                self.append(b'{"type":"response_item","payload":{"text":"sample content"}}\n')
                self.assertEqual(reader.read()[0], 'working')
            self.assertGreater(reader.record['offset'], 512)
            self.assertEqual(EventTail(self.path).read()[0], 'unknown')
            restarted = EventTail(self.path, copy.deepcopy(reader.record))
            self.assertEqual(restarted.read()[0], 'working')
            self.assertEqual(restarted.offset, self.path.stat().st_size)

    def test_newer_completion_wins_over_cached_work(self):
        checkpoint = self.checkpoint('task_started')
        self.append(event('task_complete'))
        self.assertEqual(EventTail(self.path, checkpoint).read()[0], 'complete')

    def test_cold_tail_at_line_boundary_keeps_first_complete_event(self):
        prefix = b'{"type":"response_item"}\n' * 20
        suffix = event('task_started') + b'{"type":"response_item"}\n' * 10
        self.path.write_bytes(prefix + suffix)
        with patch('activity_state.MAX_TAIL', len(suffix)):
            self.assertEqual(EventTail(self.path).read()[0], 'working')

    def test_backlog_never_exposes_old_idle_before_newer_start(self):
        checkpoint = self.checkpoint()
        self.append(b'{"type":"response_item"}\n' * 100 + event('task_started'))
        with patch('activity_state.MAX_TAIL', 512):
            reader = EventTail(self.path, checkpoint)
            self.assertEqual(reader.read(), ('unknown', None))
            self.assertEqual(self.read_to_end(reader)[0], 'working')

    def test_partial_record_is_replayed_after_restart_without_saving_its_text(self):
        checkpoint = self.checkpoint()
        start = event('task_started')
        self.append(start[:30])
        reader = EventTail(self.path, checkpoint)
        self.assertEqual(reader.read()[0], 'unknown')
        self.assertEqual(reader.record, checkpoint)
        restarted = EventTail(self.path, copy.deepcopy(reader.record))
        self.assertEqual(restarted.read()[0], 'unknown')
        self.append(start[30:])
        self.assertEqual(restarted.read()[0], 'working')

    def test_restart_cannot_rejuvenate_stale_work(self):
        self.checkpoint('task_started')
        os.utime(self.path, (time.time() - 600, time.time() - 600))
        reader = EventTail(self.path)
        self.assertEqual(reader.read()[0], 'unknown')
        self.assertEqual(EventTail(self.path, reader.record).read()[0], 'unknown')

    def test_replaced_file_does_not_inherit_old_completion(self):
        checkpoint = self.checkpoint()
        replacement = self.path.with_suffix('.new')
        replacement.write_bytes(b'{"type":"response_item"}\n')
        replacement.replace(self.path)
        self.assertEqual(EventTail(self.path, checkpoint).read()[0], 'unknown')

    def test_truncated_file_does_not_inherit_old_completion(self):
        checkpoint = self.checkpoint()
        self.path.write_bytes(b'{}\n')
        self.assertEqual(EventTail(self.path, checkpoint).read()[0], 'unknown')

    def test_same_inode_edited_event_is_rejected_even_with_restored_mtime(self):
        checkpoint = self.checkpoint()
        old_stat = self.path.stat()
        original = self.path.read_bytes()
        self.path.write_bytes(original.replace(b'task_complete', b'not_lifecycle'))
        os.utime(self.path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertEqual(EventTail(self.path, checkpoint).read()[0], 'unknown')

    def test_head_and_cursor_anchors_detect_regrown_history(self):
        reader = EventTail(self.path)
        reader.read()
        self.append(b'{"type":"response_item"}\n' * 100)
        reader.read()
        checkpoint = reader.record
        body = self.path.read_bytes()
        self.path.write_bytes(b' ' + body[1:] + b'{}\n')
        with patch('activity_state.MAX_TAIL', 512):
            self.assertEqual(EventTail(self.path, checkpoint).read()[0], 'unknown')

    def test_live_in_place_edit_invalidates_reader_too(self):
        reader = EventTail(self.path)
        reader.read()
        self.path.write_bytes(b'{"type":"response_item"}\n' * 10)
        self.assertEqual(reader.read()[0], 'unknown')
        self.assertEqual(reader.read()[0], 'unknown')

    def test_invalid_checkpoint_shapes_fall_back_without_crashing(self):
        checkpoint = self.checkpoint()
        self.append(event('task_started'))
        for bad in [None, [], 'bad', {'offset': -1}, {**checkpoint, 'offset': True},
                    {**checkpoint, 'eventLength': 99999999}, {**checkpoint, 'eventHash': 'wrong'}]:
            with self.subTest(bad=type(bad).__name__):
                self.assertEqual(EventTail(self.path, bad).read()[0], 'working')

    def test_malformed_record_does_not_leave_a_cached_idle_claim(self):
        checkpoint = self.checkpoint()
        self.append(b'not-json\n')
        reader = EventTail(self.path, checkpoint)
        self.assertEqual(reader.read()[0], 'unknown')
        self.assertIsNone(reader.record)
        self.append(event('task_started'))
        self.assertEqual(reader.read()[0], 'working')

    def test_future_or_missing_lifecycle_timestamp_is_unknown(self):
        for stamp in [None, 'invalid', '2999-01-01T00:00:00Z']:
            self.path.write_text(json.dumps({'type':'event_msg', 'timestamp':stamp,
                                           'payload':{'type':'task_started'}}) + '\n')
            self.assertEqual(EventTail(self.path).read()[0], 'unknown')

    def test_oversized_line_is_bounded_and_cannot_preserve_old_idle(self):
        checkpoint = self.checkpoint()
        self.append(b'X' * 2000 + b'\n' + event('task_started'))
        with patch('activity_state.MAX_TAIL', 512):
            reader = EventTail(self.path, checkpoint)
            for _ in range(3):
                self.assertEqual(reader.read()[0], 'unknown')
                self.assertLessEqual(len(reader.pending), 512)
            self.assertEqual(self.read_to_end(reader)[0], 'working')

    def test_missing_or_nonregular_history_never_restores_cached_status(self):
        checkpoint = self.checkpoint()
        self.path.unlink()
        with self.assertRaises(OSError):
            EventTail(self.path, checkpoint).read()
        os.mkfifo(self.path)
        with self.assertRaises(OSError):
            EventTail(self.path, checkpoint).read()


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'activity-checkpoints.json'

    def test_private_atomic_save_and_no_rewrite_when_unchanged(self):
        store = ActivityCheckpoints(self.path)
        store.save({'sample': {'state':'working'}})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ActivityCheckpoints(self.path).entries, store.entries)
        with patch('activity_state.tempfile.mkstemp') as create:
            store.save(store.entries)
            create.assert_not_called()

    def test_corrupt_or_oversized_cache_is_ignored(self):
        for body in [b'bad', b'[]', b'{}', b'X' * (MAX_CHECKPOINT_BYTES + 1)]:
            self.path.write_bytes(body)
            self.path.chmod(0o600)
            self.assertEqual(ActivityCheckpoints(self.path).entries, {})

    def test_symlink_and_world_readable_cache_are_not_read_or_overwritten(self):
        target = self.path.with_name('other.json')
        target.write_text('{"version":1,"entries":{"private":"value"}}')
        target.chmod(0o600)
        self.path.symlink_to(target)
        store = ActivityCheckpoints(self.path)
        self.assertEqual(store.entries, {})
        store.save({'test':1})
        self.assertIn('private', target.read_text())
        self.path.unlink()
        self.path.write_text(target.read_text())
        self.path.chmod(0o644)
        self.assertEqual(ActivityCheckpoints(self.path).entries, {})
        ActivityCheckpoints(self.path).save({'test':1})
        self.assertIn('private', self.path.read_text())

    def test_failed_save_keeps_previous_cache_and_cleans_temporary(self):
        store = ActivityCheckpoints(self.path)
        store.save({'sample':1})
        before = self.path.read_bytes()
        with patch('activity_state.os.replace', side_effect=OSError('test')):
            store.save({'sample':2})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.activity-*.tmp')), [])

    def test_capacity_limits_preserve_previous_file(self):
        store = ActivityCheckpoints(self.path)
        store.save({'sample':1})
        before = self.path.read_bytes()
        store.save({str(i): i for i in range(7)})
        self.assertEqual(self.path.read_bytes(), before)
        store.save({'sample':'x' * MAX_CHECKPOINT_BYTES})
        self.assertEqual(self.path.read_bytes(), before)


class OfficeCheckpointTests(unittest.TestCase):
    setUp = fixtures.OfficeTests.setUp
    write_app = fixtures.OfficeTests.write_app
    add_task = fixtures.OfficeTests.add_task
    def test_checkpoint_is_private_metadata_and_survives_office_restart(self):
        from server import OfficeState
        path = self.add_task('one', 100)
        cache = self.root / 'activity-checkpoints.json'
        state = OfficeState(self.root, activity_path=cache)
        self.assertEqual(state.snapshot()['slots'][0]['state'], 'working')
        with patch('activity_state.MAX_TAIL', 512):
            for _ in range(40):
                with path.open('ab') as stream:
                    stream.write(b'{"type":"response_item","payload":{"text":"PRIVATE CONTENT"}}\n')
                self.assertEqual(state.snapshot()['slots'][0]['state'], 'working')
            restarted = OfficeState(self.root, activity_path=cache)
            self.assertEqual(restarted.snapshot()['slots'][0]['state'], 'working')
        text = cache.read_text()
        for private in ['PRIVATE CONTENT', str(path), '"one"', 'payload']:
            self.assertNotIn(private, text)
        self.assertLess(len(text), 1024)
        self.assertNotIn('eventHash', json.dumps(state.snapshot()))

    def test_old_paths_and_unpinned_tasks_are_pruned(self):
        from server import OfficeState
        self.add_task('one', 100)
        cache = self.root / 'activity-checkpoints.json'
        state = OfficeState(self.root, activity_path=cache)
        state.snapshot()
        with sqlite3.connect(self.db) as con:
            con.execute('DELETE FROM threads')
        state.snapshot()
        self.assertEqual(ActivityCheckpoints(cache).entries, {})

    def test_read_only_diagnostic_mode_never_writes_cache(self):
        from server import OfficeState
        self.add_task('one', 100)
        with patch('activity_state.tempfile.mkstemp') as create:
            self.assertTrue(OfficeState(self.root).snapshot()['connected'])
            create.assert_not_called()

    def test_missing_history_and_failed_cache_save_do_not_break_viewer(self):
        from server import OfficeState
        path = self.add_task('one', 100)
        cache = self.root / 'activity-checkpoints.json'
        state = OfficeState(self.root, activity_path=cache)
        with patch('activity_state.os.replace', side_effect=OSError('test')):
            self.assertEqual(state.snapshot()['slots'][0]['state'], 'working')
        path.unlink()
        snapshot = state.snapshot()
        self.assertTrue(snapshot['connected'])
        self.assertEqual(snapshot['slots'][0]['state'], 'unknown')

    def test_checkpoint_file_is_not_an_http_route(self):
        import io
        from server import make_handler
        handler_type = make_handler(None, 4318)
        handler = handler_type.__new__(handler_type)
        handler.headers = {'Host':'localhost:4318'}
        handler.path = '/activity-checkpoints.json'
        handler.wfile = io.BytesIO()
        errors = []
        handler.send_error = errors.append
        handler.do_GET()
        self.assertEqual(errors, [404])
        self.assertEqual(handler.wfile.getvalue(), b'')


if __name__ == '__main__':
    unittest.main()
