import copy
import json
from pathlib import Path
import socket
import sqlite3
import struct
import tempfile
import threading
import unittest

from probe import (Asar, Frames, MAX_FRAME, device_inventory, encode, event_name,
                   observe, press_candidate, read_pins, token)


PINS = ["test-agent-" + str(i) for i in range(6)]


def press(slot=0, **overrides):
    event = {"key": "AG0" + str(slot), "act": 1, "slot": slot,
             "threadKey": "local:" + PINS[slot]}
    event.update(overrides)
    return {"type": "codex-micro-hid-event", "event": event}


def candidate(message, pins=None, **kwargs):
    return press_candidate(message, PINS if pins is None else pins,
                           mode=kwargs.get("mode", "pinned"),
                           snapshot_age_ms=kwargs.get("age", 0))


class MappingTests(unittest.TestCase):
    def test_all_six_keys_and_no_false_selection_confirmation(self):
        for slot in range(6):
            result = candidate(press(slot))
            self.assertEqual(result["key"], slot + 1)
            self.assertEqual(result["agentToken"], token(PINS[slot]))
            self.assertFalse(result["selectionConfirmed"])

    def test_unknown_release_command_and_invalid_types_ignored(self):
        cases = [None, {}, {"type": "other"}, {"type": "codex-micro-hid-event", "event": None}]
        cases += [press(act=act) for act in [0, 2, None, True, "1"]]
        cases += [press(key=key) for key in ["AG06", "AG0", "AG00\n", "MIC", 0]]
        cases += [press(slot=0, **{name: value}) for name, value in
                  [("threadKey", "remote:test-agent-0"), ("threadKey", None)]]
        for message in cases:
            self.assertIsNone(candidate(message))

    def test_slot_must_match_hardware_key(self):
        message = press()
        for slot in [True, "0", -1, 1, 6]:
            message["event"]["slot"] = slot
            self.assertIsNone(candidate(message))

    def test_stale_mismatched_or_unsupported_roster_rejected(self):
        for age in [-1, 1001, True, float("inf"), float("nan")]:
            self.assertIsNone(candidate(press(), age=age))
        for mode in ["custom", "recent", "priority", None]:
            self.assertIsNone(candidate(press(), mode=mode))
        for pins in [[], [None], PINS + ["extra"], [PINS[0]] * 6, list(reversed(PINS))]:
            self.assertIsNone(candidate(press(), pins=pins))

    def test_reorder_changes_key_but_preserves_identity(self):
        reordered = [PINS[1], PINS[0], *PINS[2:]]
        message = press(1, threadKey="local:" + PINS[0])
        result = candidate(message, pins=reordered)
        self.assertEqual(result["key"], 2)
        self.assertEqual(result["agentToken"], candidate(press())["agentToken"])

    def test_unassigned_slot_and_private_fields_never_emitted(self):
        self.assertIsNone(candidate(press(5), pins=PINS[:5]))
        message = press(agent=99, privateText="SECRET", title="SECRET")
        before = copy.deepcopy(message)
        result = candidate(message)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn(PINS[0], json.dumps(result))
        self.assertEqual(message, before)


class InventoryTests(unittest.TestCase):
    def test_filter_vendor_products_and_redact_device_identifiers(self):
        device = {"VendorID": 12346, "ProductID": 33431, "PrimaryUsagePage": 65280,
                  "PrimaryUsage": 1, "Transport": "USB", "SerialNumber": "SECRET"}
        tree = [{"IORegistryEntryChildren": [device, dict(device),
                 {"VendorID": 1, "ProductID": 33431, "SerialNumber": "SECRET"}]}]
        result = device_inventory(tree)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model"], "Creator Micro 2")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_both_micro_models_and_missing_device(self):
        result = device_inventory([{"VendorID": 12346, "ProductID": pid}
                                   for pid in [33632, 33431, 33432, 4]])
        self.assertEqual(len(result), 3)
        self.assertEqual(device_inventory([]), [])


class ProtocolTests(unittest.TestCase):
    def test_partial_header_body_and_multiple_frames(self):
        decoder = Frames()
        data = encode({"type": "one"}) + encode({"type": "two"})
        results = []
        for byte in data:
            results.extend(decoder.feed(bytes([byte])))
        self.assertEqual(results, [{"type": "one"}, {"type": "two"}])
        self.assertEqual(decoder.buffer, bytearray())

    def test_invalid_lengths_json_and_envelope(self):
        for size in [0, MAX_FRAME + 1]:
            with self.assertRaises(ValueError):
                Frames().feed(struct.pack("<I", size))
        for data in [b"?", b"[]"]:
            with self.assertRaises(ValueError):
                Frames().feed(struct.pack("<I", len(data)) + data)

    def test_method_inventory_retains_no_payload(self):
        message = {"type": "broadcast", "method": "thread-stream-state-changed",
                   "params": {"private": "SECRET"}}
        self.assertEqual(event_name(message), "broadcast:thread-stream-state-changed")
        message["method"] = "private/path/value"
        self.assertEqual(event_name(message), "broadcast:other")

    def test_real_unix_socket_declines_requests_and_retains_counts_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.sock"
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(path))
            path.chmod(0o600)
            listener.listen(1)
            captured, errors = [], []

            def serve():
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(2)
                        decoder = Frames()
                        while not captured:
                            captured.extend(decoder.feed(conn.recv(65536)))
                        response = {"type": "response", "requestId": captured[0]["requestId"],
                                    "result": {"clientId": "private-client"}}
                        discovery = {"type": "client-discovery-request", "requestId": "request-test",
                                     "request": {"method": "private-action", "params": "SECRET"}}
                        broadcast = {"type": "broadcast", "method": "thread-stream-state-changed",
                                     "params": {"turnHistory": "SECRET"}}
                        conn.sendall(encode(response) + encode(discovery) + encode(broadcast))
                        while len(captured) < 2:
                            captured.extend(decoder.feed(conn.recv(65536)))
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                result = observe(path, 2)
            finally:
                worker.join(timeout=3)
                listener.close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(captured[0]["method"], "initialize")
            self.assertEqual(captured[1], {"type": "client-discovery-response",
                             "requestId": "request-test", "response": {"canHandle": False}})
            self.assertEqual(len(captured), 2)
            self.assertEqual(result["subscriptionsSent"], 0)
            self.assertEqual(result["state"], "disconnected")
            self.assertIsNotNone(result["handshakeMs"])
            self.assertIsNone(result["keyboardLatencyMs"])
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertNotIn("private-client", json.dumps(result))

    def test_symlink_socket_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ordinary"
            path.write_text("not a socket")
            alias = Path(folder) / "link"
            alias.symlink_to(path)
            with self.assertRaises(ValueError):
                observe(alias, 1)


class LocalFileTests(unittest.TestCase):
    def test_pins_query_uses_section_order_and_ignores_archived(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            con = sqlite3.connect(root / "state_5.sqlite")
            con.executescript("CREATE TABLE thread_sections(id TEXT,name TEXT);"
                              "CREATE TABLE threads(id TEXT,thread_section_id TEXT,archived INT,section_position INT);"
                              "INSERT INTO thread_sections VALUES('p','Pinned');")
            con.executemany("INSERT INTO threads VALUES(?,?,?,?)",
                            [("b", "p", 0, 2), ("old", "p", 1, 0), ("a", "p", 0, 1)])
            con.commit()
            con.close()
            self.assertEqual(read_pins(root), ["a", "b"])

    def test_asar_bounded_read_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            content = b'{"version":"test"}'
            tree = {"files": {"package.json": {"offset": "0", "size": len(content)}}}
            header = json.dumps(tree).encode()
            padded = (len(header) + 3) // 4 * 4
            size = padded + 8
            path = Path(folder) / "app.asar"
            path.write_bytes(struct.pack("<IIII", 4, size, padded + 4, len(header)) +
                             header + b"\0" * (padded - len(header)) + content)
            archive = Asar(path)
            name, item = list(archive.files())[0]
            self.assertEqual(name, "package.json")
            self.assertEqual(archive.read(item), content)
            path.write_bytes(path.read_bytes()[:-2])
            with self.assertRaises(ValueError):
                archive.read(item)


if __name__ == "__main__":
    unittest.main()
