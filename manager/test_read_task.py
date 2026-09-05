import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from read_task import parse_turns, read_task


def event(kind, **payload):
    return json.dumps({"type": "event_msg", "timestamp": "2026-09-05T22:30:00Z", "payload": {"type": kind, **payload}})


def message(role, text, **extra):
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                     "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}], **extra}})


class ReadTaskTests(unittest.TestCase):
    def test_public_messages_only_and_no_context_injection(self):
        lines = [event("task_started", turn_id="new"), message("developer", "PRIVATE CONFIG"),
                 message("user", "<environment_context>LOCAL CONFIG"), message("user", "Fix the office"),
                 message("assistant", "HIDDEN REASONING", phase="analysis"),
                 message("assistant", "Fixed and checked", phase="final_answer"),
                 event("task_complete", turn_id="new")]
        result = parse_turns(lines)
        text = json.dumps(result)
        self.assertNotIn("CONFIG", text)
        self.assertNotIn("REASONING", text)
        self.assertEqual([m["text"] for m in result[0]["messages"]], ["Fix the office", "Fixed and checked"])
        self.assertEqual(result[0]["status"], "completed")

    def test_delegation_is_distinct_from_user_permission(self):
        payload = {"type": "function_call_output", "namespace": "codex_app", "name": "send_message_to_thread",
                   "output": "<codex_delegation><input>Proceed</input></codex_delegation>"}
        result = parse_turns([json.dumps({"type": "response_item", "payload": payload})])
        self.assertEqual(result[0]["messages"][0]["role"], "delegated_input")
        self.assertTrue(result[0]["partial"])

    def test_long_run_preserves_request_and_marks_truncation(self):
        lines = [event("task_started", turn_id="one"), message("user", "x" * 200)]
        lines += [message("assistant", str(i), phase="commentary") for i in range(20)]
        result = parse_turns(lines, max_chars=100)[0]
        self.assertEqual(result["messages"][0]["role"], "user")
        self.assertTrue(result["messages"][0]["truncated"])
        self.assertEqual(result["omittedMessageCount"], 14)
        self.assertEqual(result["messages"][-1]["text"], "19")

    def test_latest_turn_and_malformed_line(self):
        lines = [event("task_started", turn_id="old"), message("user", "Old request"),
                 event("task_complete"), event("task_started", turn_id="new"),
                 b'{"unfinished":', message("user", "Current request")]
        result = parse_turns(lines, count=1)
        self.assertEqual(result[0]["turnId"], "new")
        self.assertEqual(result[0]["messages"][0]["text"], "Current request")

    def test_out_of_roster_is_rejected_before_database_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'team.json').write_text('{"members":[]}')
            with patch('read_task.ROOT',root), self.assertRaisesRegex(ValueError, "outside"):
                read_task("unrelated-task")

    def test_midturn_user_steering_is_visible(self):
        lines=[event('task_started',turn_id='one'), message('user','Start work'),
               event('item_completed',item={'type':'UserMessage','content':[{'type':'Text','text':'Keep the original files'}]})]
        result=parse_turns(lines)
        self.assertEqual(result[0]['messages'][-1]['text'],'Keep the original files')


if __name__ == "__main__":
    unittest.main()
