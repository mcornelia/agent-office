import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from communications import CommunicationFeed, communication_events


def record(tool='read_thread', args=None, at=1000, status='completed'):
    return {'type':'event_msg','timestamp':datetime.fromtimestamp(at,timezone.utc).isoformat(),
            'payload':{'type':'item_completed','item':{'type':'McpToolCall','server':'codex_app',
            'tool':tool,'id':'test-call','status':status,'arguments':args or {'threadId':'worker'},
            'result':{'content':[{'text':'PRIVATE RESULT'}]}}}}


class CommunicationTests(unittest.TestCase):
    def test_only_metadata_reaches_the_page(self):
        event=record('send_message_to_thread',{'threadId':'worker','prompt':'PRIVATE PROMPT'})
        result=communication_events(event,'manager',{'worker'},1001)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['kind'],'message')
        self.assertEqual(set(result[0]),{'id','from','to','kind','at'})
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_batch_checks_are_scoped_and_deduplicated(self):
        event=record('wait_threads',{'targets':[{'threadId':x} for x in ('worker','worker','outsider','manager')]})
        result=communication_events(event,'manager',{'worker'},1001)
        self.assertEqual([item['to'] for item in result],['worker'])

    def test_old_future_and_failed_events_do_not_animate(self):
        for event in (record(at=900),record(at=1010),record(status='failed')):
            self.assertEqual(communication_events(event,'manager',{'worker'},1001),[])
        event=record();event['payload']['item']['result']['isError']=True
        self.assertEqual(communication_events(event,'manager',{'worker'},1001),[])

    def test_unrelated_tools_and_remote_targets_are_ignored(self):
        for event in (record('create_thread'),record(args={'threadId':'worker','hostId':'remote'}),
                      record('wait_threads',{'targets':None})):
            self.assertEqual(communication_events(event,'manager',{'worker'},1001),[])

    def test_overview_has_no_invented_recipient(self):
        result=communication_events(record('list_threads',{'limit':1}),'manager',{'worker'},1001)
        self.assertEqual(result[0]['kind'],'overview')
        self.assertIsNone(result[0]['to'])

    def test_partial_append_expiry_and_roster_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir()
            roster=root/'team.json'
            roster.write_text(json.dumps({'managerThreadId':'manager','members':[{'threadId':'manager'},{'threadId':'worker'}]}))
            path=root/'sessions'/'manager.jsonl';path.write_bytes(b'')
            rows=[{'id':'manager','rollout_path':str(path)},{'id':'worker','rollout_path':str(root/'sessions'/'worker.jsonl')}]
            feed=CommunicationFeed(root,roster)
            line=json.dumps(record()).encode()+b'\n'
            path.write_bytes(line[:70])
            self.assertEqual(feed.snapshot(rows,1001),[])
            with path.open('ab') as stream:stream.write(line[70:])
            first=feed.snapshot(rows,1001)
            self.assertEqual(len(first),1)
            self.assertEqual(feed.snapshot(rows,1002),first)
            self.assertEqual(feed.snapshot(rows,1046),[])
            self.assertEqual(feed.snapshot(rows[:1],1001),[])

    def test_session_replacement_and_corrupt_roster_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'sessions').mkdir()
            roster=root/'team.json';roster.write_text(json.dumps({'managerThreadId':'manager','members':[{'threadId':'worker'}]}))
            path=root/'sessions'/'manager.jsonl';path.write_text(json.dumps(record())+'\n')
            rows=[{'id':'manager','rollout_path':str(path)},{'id':'worker'}]
            feed=CommunicationFeed(root,roster)
            self.assertEqual(len(feed.snapshot(rows,1001)),1)
            replacement=path.with_suffix('.tmp');replacement.write_text('')
            replacement.replace(path)
            self.assertEqual(feed.snapshot(rows,1001),[])
            roster.write_text('broken')
            self.assertEqual(feed.snapshot(rows,1001),[])


if __name__=='__main__':
    unittest.main()
