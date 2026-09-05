#!/usr/bin/env python3
"""Read-only feasibility probes, not a production selection source.

No HID handles, lighting writes, UI automation, prompts, or task subscriptions.
The IPC probe retains method counts only, never message payloads or transcripts.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import sqlite3
import stat
import struct
import subprocess
import time
import uuid

VENDOR = 12346
PRODUCTS = {33632: "Codex Micro", 33431: "Creator Micro 2", 33432: "Creator Micro 2"}
MAX_FRAME = 32 * 1024 * 1024
MAX_HEADER = 16 * 1024 * 1024
MAX_SOURCE = 16 * 1024 * 1024
AGENT_KEY = re.compile(r"AG0([0-5])\Z")
METHOD = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")


def token(thread_id):
    return hashlib.sha256(thread_id.encode()).hexdigest()[:12]


def device_inventory(tree):
    """Filter the IORegistry before emitting anything; omit serials and paths."""
    devices = {}

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            product = value.get("ProductID")
            if value.get("VendorID") == VENDOR and product in PRODUCTS:
                entry = {
                    "model": PRODUCTS[product], "vendorId": VENDOR,
                    "productId": product, "usagePage": value.get("PrimaryUsagePage"),
                    "usage": value.get("PrimaryUsage"), "transport": value.get("Transport"),
                }
                devices[json.dumps(entry, sort_keys=True)] = entry
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(tree)
    return sorted(devices.values(), key=lambda item: (item["productId"], str(item["usagePage"])))


def inventory():
    result = subprocess.run(
        ["/usr/sbin/ioreg", "-a", "-r", "-c", "IOHIDDevice", "-l"],
        check=True, capture_output=True, timeout=15,
    )
    devices = device_inventory(plistlib.loads(result.stdout))
    return {"compatibleInterfaces": devices, "compatibleDeviceDetected": bool(devices),
            "openedHidDevice": False}


def read_pins(codex_dir):
    """Reuse current local pin ordering, without opening any session files.

    This is the office's local-task roster, not proof of the Micro's current
    assignments. Pinned projects, remote tasks, and custom mapping require the
    desktop's resolved slot map; do not infer them from this database.
    """
    candidates = [p for p in codex_dir.glob("state_*.sqlite")
                  if re.fullmatch(r"state_\d+", p.stem)]
    if not candidates:
        raise ValueError("No local task database")
    path = max(candidates, key=lambda p: int(p.stem.split("_")[-1])).resolve()
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        con.execute("PRAGMA query_only=ON")
        sections = con.execute("SELECT id FROM thread_sections WHERE name='Pinned'").fetchall()
        if len(sections) != 1:
            raise ValueError("Exactly one local Pinned section is required")
        rows = con.execute(
            "SELECT id FROM threads WHERE thread_section_id=? AND archived=0 "
            "ORDER BY section_position ASC,id ASC LIMIT 6", (sections[0][0],),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        con.close()


def press_candidate(message, pins, *, mode, snapshot_age_ms):
    """Validate an observed *renderer* event against a fresh, known pinned map.

    Used only by synthetic tests/replay until an authorized live transport is
    available. Its return value never establishes successful UI selection.
    """
    if mode != "pinned" or not isinstance(message, dict):
        return None
    if type(snapshot_age_ms) not in (int, float) or not 0 <= snapshot_age_ms <= 1000:
        return None
    if not isinstance(pins, list) or not 1 <= len(pins) <= 6:
        return None
    if not all(isinstance(p, str) and p for p in pins) or len(set(pins)) != len(pins):
        return None
    if message.get("type") != "codex-micro-hid-event":
        return None
    event = message.get("event")
    if not isinstance(event, dict) or type(event.get("act")) is not int or event["act"] != 1:
        return None
    key = event.get("key")
    match = AGENT_KEY.fullmatch(key) if isinstance(key, str) else None
    if not match:
        return None
    slot = int(match[1])
    if type(event.get("slot")) is not int or event["slot"] != slot or slot >= len(pins):
        return None
    if event.get("threadKey") != "local:" + pins[slot]:
        return None
    return {"key": slot + 1, "agentToken": token(pins[slot]),
            "source": "renderer-hid-event", "selectionConfirmed": False}


class Frames:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        messages = []
        while len(self.buffer) >= 4:
            size = struct.unpack("<I", self.buffer[:4])[0]
            if not 0 < size <= MAX_FRAME:
                raise ValueError("Invalid frame size")
            if len(self.buffer) < size + 4:
                break
            message = json.loads(bytes(self.buffer[4:4 + size]))
            del self.buffer[:4 + size]
            if not isinstance(message, dict):
                raise ValueError("Invalid message envelope")
            messages.append(message)
        return messages


def encode(message):
    body = json.dumps(message).encode()
    return struct.pack("<I", len(body)) + body


def event_name(message):
    kind = message.get("type")
    if kind == "broadcast":
        method = message.get("method")
        return "broadcast:" + (method if isinstance(method, str) and METHOD.fullmatch(method) else "other")
    return kind if kind in {"response", "request", "client-discovery-request"} else "other"


def observe(path, seconds):
    """Only initialize and decline capability discovery; no subscriptions.

    A successful handshake measures local socket round-trip time, not keyboard
    latency. Even zero observed key events cannot prove keys emit no events.
    """
    metadata = path.lstat()
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("Socket must be owned by this user and not be a symlink")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Socket must not be accessible to other users")
    started = time.monotonic()
    deadline = started + seconds
    request_id = str(uuid.uuid4())
    sent = Counter()
    received = Counter()
    handshake_ms = None
    max_gap_ms = 0.0
    last_received = None
    state = "observation-complete"
    decoder = Frames()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(min(seconds, 0.25))
        connection.connect(str(path))
        connection.sendall(encode({
            "type": "request", "requestId": request_id, "method": "initialize",
            "version": 0, "params": {"clientType": "agent-office-keyboard-probe"},
        }))
        sent["initialize"] += 1
        while time.monotonic() < deadline:
            if handshake_ms is None and time.monotonic() - started > min(5, seconds):
                state = "initialization-timeout"
                break
            connection.settimeout(max(0.001, min(0.25, deadline - time.monotonic())))
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                state = "disconnected"
                break
            now = time.monotonic()
            if last_received is not None:
                max_gap_ms = max(max_gap_ms, (now - last_received) * 1000)
            last_received = now
            for message in decoder.feed(chunk):
                received[event_name(message)] += 1
                if message.get("type") == "response" and message.get("requestId") == request_id:
                    result = message.get("result")
                    if not isinstance(result, dict) or not isinstance(result.get("clientId"), str):
                        raise ValueError("Initialization refused or unsupported")
                    handshake_ms = (now - started) * 1000
                elif message.get("type") == "client-discovery-request":
                    rid = message.get("requestId")
                    if isinstance(rid, str) and len(rid) <= 128:
                        connection.sendall(encode({
                            "type": "client-discovery-response", "requestId": rid,
                            "response": {"canHandle": False},
                        }))
                        sent["negative-capability-response"] += 1
                # Do not retain or emit params, snapshots, titles, or IDs.
                del message
    if handshake_ms is None and state == "observation-complete":
        state = "initialization-timeout"
    return {"state": state, "elapsedSeconds": round(time.monotonic() - started, 3),
            "handshakeMs": round(handshake_ms, 3) if handshake_ms is not None else None,
            "received": dict(sorted(received.items())), "sent": dict(sent),
            "maxObservedMessageGapMs": round(max_gap_ms, 3),
            "keyboardLatencyMs": None, "selectionConfirmed": False,
            "subscriptionsSent": 0, "payloadsRetained": False}


class Asar:
    """Bounded reader for selected source files in the installed app archive."""
    def __init__(self, path):
        self.path = path
        with path.open("rb") as stream:
            header = stream.read(16)
            if len(header) != 16:
                raise ValueError("Truncated archive")
            size, self.header_size, _, length = struct.unpack("<IIII", header)
            if size != 4 or not 0 < length <= MAX_HEADER or self.header_size < length + 8:
                raise ValueError("Unsupported archive header")
            self.tree = json.loads(stream.read(length))

    def files(self, tree=None, prefix=""):
        for name, item in (self.tree if tree is None else tree).get("files", {}).items():
            path = prefix + name
            if "files" in item:
                yield from self.files(item, path + "/")
            else:
                yield path, item

    def read(self, item):
        size = item.get("size")
        if item.get("unpacked") or type(size) is not int or not 0 <= size <= MAX_SOURCE:
            raise ValueError("Unsupported source entry")
        offset = int(item["offset"])
        if offset < 0:
            raise ValueError("Negative source offset")
        with self.path.open("rb") as stream:
            stream.seek(8 + self.header_size + offset)
            content = stream.read(size)
        if len(content) != size:
            raise ValueError("Truncated source entry")
        return content


def inspect_app(path):
    archive = Asar(path)
    findings = []
    version = None
    selectors = {
        ".vite/build/main-": ["codex-micro-hid-event", "sendMessageToWindow", "handleHidEvent", "isSessionLocked"],
        ".vite/build/service-": ["onHidReceived", "AG0([0-5])", "displayedLightingModel", "threadKey"],
        "webview/assets/codex-micro-bridge-": ["codex-micro-hid-event", "t.act!==1", "t.threadKey"],
        "webview/assets/codex-micro-slot-signals-": ["pinnedThreadKeys", "selectedThreadKey", "source", "custom"],
        ".vite/build/src-": ["thread-stream-following-changed", "client-discovery-request", "codex-micro-hid-event"],
    }
    for name, item in archive.files():
        if name == "package.json":
            version = json.loads(archive.read(item)).get("version")
        if not name.endswith(".js"):
            continue
        needles = next((values for prefix, values in selectors.items() if name.startswith(prefix)), None)
        if needles is None:
            continue
        data = archive.read(item)
        source = data.decode()
        markers = {needle: needle in source for needle in needles}
        if any(markers.values()):
            findings.append({"file": name, "sha256": hashlib.sha256(data).hexdigest(), "markers": markers})
    return {"appVersion": version, "files": findings,
            "note": "Static marker inventory is evidence for manual inspection, not a supported API or live signal."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    pins = sub.add_parser("pins")
    pins.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex")
    ipc = sub.add_parser("observe-ipc")
    ipc.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex")
    ipc.add_argument("--seconds", type=float, default=15)
    app = sub.add_parser("inspect-app")
    app.add_argument("--asar", type=Path, default=Path("/Applications/ChatGPT.app/Contents/Resources/app.asar"))
    replay = sub.add_parser("replay")
    replay.add_argument("fixture", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result = inventory()
        elif args.command == "pins":
            result = {"localOfficePins": [{"key": i + 1, "agentToken": token(p)}
                                         for i, p in enumerate(read_pins(args.codex_dir.expanduser()))],
                      "microAssignmentsConfirmed": False}
        elif args.command == "observe-ipc":
            if not 0 < args.seconds <= 60:
                parser.error("--seconds must be greater than zero and at most 60")
            result = observe(args.codex_dir.expanduser() / "ipc/ipc.sock", args.seconds)
        elif args.command == "inspect-app":
            result = inspect_app(args.asar.expanduser())
        else:
            fixture = json.loads(args.fixture.read_text())
            result = {"synthetic": True, "candidates": [press_candidate(event, fixture["pins"],
                       mode=fixture["mode"], snapshot_age_ms=0) for event in fixture["events"]]}
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as exc:
        # Avoid embedding private paths or provider message payloads in reports.
        print(json.dumps({"state": "unavailable", "errorType": type(exc).__name__}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
