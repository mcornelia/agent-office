"""Read-only observer of Codex desktop's local task-status broadcasts.

This is an observed internal protocol, not a public API. Only initialization,
status subscriptions, and negative capability-discovery replies are sent.
Conversation content is discarded; only runtime status and revision are kept.
"""
import copy
import json
import socket
import stat
import struct
import threading
import time
import uuid

MAX_FRAME = 32 * 1024 * 1024
WAIT_FLAGS = {"waitingOnApproval", "waitingOnUserInput"}


def public_status(value):
    if not isinstance(value, dict) or value.get("type") not in ("active", "idle", "notLoaded", "systemError"):
        return None
    flags = value.get("activeFlags", [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        return None
    return {"type": value["type"], "activeFlags": flags}


def reduce_status(previous, change):
    """Keep only runtime status, applying contiguous status patches."""
    if change.get("type") == "snapshot":
        status = public_status(change.get("conversationState", {}).get("threadRuntimeStatus"))
        return {"revision": change.get("revision"), "status": status} if status else None
    if change.get("type") != "patches" or previous is None or change.get("baseRevision") != previous["revision"]:
        return None
    status = copy.deepcopy(previous["status"])
    try:
        for patch in change.get("patches", []):
            path = patch.get("path", [])
            if not path:
                return None
            if path[0] != "threadRuntimeStatus":
                continue
            op, value = patch.get("op"), patch.get("value")
            if len(path) == 1:
                status = copy.deepcopy(value) if op in ("add", "replace") else None
            elif len(path) == 2 and path[1] in ("type", "activeFlags"):
                if op == "remove":
                    status.pop(path[1], None)
                else:
                    status[path[1]] = copy.deepcopy(value)
            elif len(path) == 3 and path[1] == "activeFlags":
                flags, index = status["activeFlags"], int(path[2])
                if index < 0:
                    return None
                if op == "remove":
                    flags.pop(index)
                elif op == "add":
                    if index > len(flags):
                        return None
                    flags.insert(index, value)
                elif op == "replace":
                    flags[index] = value
                else:
                    return None
            else:
                return None
    except (TypeError, KeyError, IndexError, ValueError):
        return None
    status = public_status(status)
    return {"revision": change.get("revision"), "status": status} if status else None


class DesktopStatus:
    def __init__(self, codex_dir):
        self.path = codex_dir / "ipc" / "ipc.sock"
        self.lock = threading.Lock()
        self.targets = set()
        self.states = {}
        self.connected = False
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._run, name="office-status", daemon=True)

    def start(self):
        self.worker.start()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)

    def follow(self, ids):
        with self.lock:
            self.targets = set(ids)
            self.states = {key: value for key, value in self.states.items() if key in self.targets}

    def get(self, thread_id):
        with self.lock:
            entry = self.states.get(thread_id) if self.connected else None
            return copy.deepcopy(entry["status"]) if entry else None

    def _run(self):
        while not self.stop.is_set():
            try:
                self._observe()
            except (OSError, ValueError, TypeError, KeyError, EOFError):
                pass
            finally:
                with self.lock:
                    self.connected = False
                    self.states.clear()
            self.stop.wait(1)

    def _observe(self):
        import os
        metadata = self.path.lstat()
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ValueError("Desktop socket is not owned by this user")
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(0.5)
            connection.connect(str(self.path))

            def send(message):
                body = json.dumps(message).encode()
                connection.sendall(struct.pack("<I", len(body)) + body)

            def follow(thread_id, following=True):
                send({"type": "broadcast", "method": "thread-stream-following-changed",
                      "sourceClientId": client_id, "version": 1,
                      "params": {"conversationId": thread_id, "hostId": "local", "following": following}})

            request_id = str(uuid.uuid4())
            send({"type": "request", "requestId": request_id, "method": "initialize",
                  "version": 0, "params": {"clientType": "agent-office"}})
            client_id, subscribed, buffer = None, set(), bytearray()
            initialized_by = time.monotonic() + 5
            refresh_at = 0
            while not self.stop.is_set():
                if client_id:
                    with self.lock:
                        targets = set(self.targets)
                    for key in subscribed - targets:
                        follow(key, False)
                    refresh = time.monotonic() >= refresh_at
                    for key in targets if refresh else targets - subscribed:
                        follow(key)
                    subscribed = targets
                    if refresh:
                        refresh_at = time.monotonic() + 30
                elif time.monotonic() > initialized_by:
                    raise ValueError("Desktop initialization timed out")
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise EOFError()
                buffer.extend(chunk)
                while len(buffer) >= 4:
                    size = struct.unpack("<I", buffer[:4])[0]
                    if not 0 < size <= MAX_FRAME:
                        raise ValueError("Unsupported desktop frame size")
                    if len(buffer) < 4 + size:
                        break
                    message = json.loads(bytes(buffer[4:4 + size]))
                    del buffer[:4 + size]
                    if message.get("type") == "response" and message.get("requestId") == request_id:
                        client_id = message.get("result", {}).get("clientId")
                        with self.lock:
                            self.connected = bool(client_id)
                    elif message.get("type") == "client-discovery-request":
                        send({"type": "client-discovery-response", "requestId": message["requestId"],
                              "response": {"canHandle": False}})
                    elif message.get("type") == "broadcast":
                        params = message.get("params", {})
                        key = params.get("conversationId")
                        if message.get("method") == "client-status-changed":
                            refresh_at = 0
                        elif key in subscribed and params.get("hostId") == "local":
                            if message.get("method") == "thread-stream-following-status-requested":
                                follow(key)
                            elif message.get("method") == "thread-stream-state-changed":
                                if message.get("version") != 11:
                                    raise ValueError("Desktop stream version changed")
                                with self.lock:
                                    reduced = reduce_status(self.states.get(key), params.get("change", {}))
                                    if reduced:
                                        self.states[key] = reduced
                                    else:
                                        self.states.pop(key, None)
                                if not reduced:
                                    refresh_at = 0
                    # Do not retain the full conversation snapshot or patches.
                    del message
            for key in subscribed:
                follow(key, False)
