"""Optional experimental desktop transport, separate from scheduling policy.

Manual is the default. This adapter speaks an observed private protocol, not
the documented Codex App Server API. Never select it as an automatic fallback.
"""
import json
import os
import socket
import stat
import struct
import time
import uuid
from pathlib import Path

MAX_FRAME = 32 * 1024 * 1024


def dispatch_mode(config):
    mode = config.get('dispatchMode', 'manual')
    if mode not in ('manual', 'desktop-experimental'):
        raise ValueError('Unsupported dispatch mode; choose manual or desktop-experimental')
    return mode


def open_transport(mode, codex_dir):
    if mode != 'desktop-experimental':
        raise ValueError('Automatic desktop dispatch requires explicit experimental opt-in')
    return DesktopRequest(Path(codex_dir) / 'ipc' / 'ipc.sock')


class DesktopRequest:
    """Bounded same-user IPC client; never handles approvals or discoveries."""
    def __init__(self, path, timeout=30):
        self.path, self.timeout = Path(path), timeout

    def __enter__(self):
        info = self.path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Desktop socket must belong to the current user')
        self.connection = socket.socket(socket.AF_UNIX)
        self.connection.settimeout(self.timeout)
        try:
            self.connection.connect(str(self.path))
            self.request('initialize', {'clientType': 'agent-office-local-watch'}, version=0)
        except Exception:
            self.connection.close()
            raise
        return self

    def __exit__(self, *_args):
        self.connection.close()

    def send(self, message):
        body = json.dumps(message).encode()
        self.connection.sendall(struct.pack('<I', len(body)) + body)

    def read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            part = self.connection.recv(size - len(data))
            if not part:
                raise EOFError('Desktop connection closed')
            data.extend(part)
        return data

    def request(self, method, params, version, target=None):
        rid = str(uuid.uuid4())
        message = {'type': 'request', 'requestId': rid, 'method': method,
                   'version': version, 'params': params}
        if target:
            message['targetClientId'] = target
        self.send(message)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.connection.settimeout(max(.01, deadline - time.monotonic()))
            size = struct.unpack('<I', self.read_exact(4))[0]
            if not 0 < size <= MAX_FRAME:
                raise ValueError('Unsupported desktop frame')
            result = json.loads(self.read_exact(size))
            if result.get('type') == 'client-discovery-request':
                self.send({'type': 'client-discovery-response', 'requestId': result['requestId'],
                           'response': {'canHandle': False}})
            if result.get('type') == 'response' and result.get('requestId') == rid:
                if result.get('resultType') != 'success' or result.get('method') != method:
                    raise RuntimeError('Desktop request was not accepted')
                return result
        raise TimeoutError('Desktop request outcome is unknown')

    def owner(self, manager_id):
        reply = self.request('thread-owner-discovery',
                             {'hostId': 'local', 'conversationId': manager_id}, version=1)
        owner = reply.get('handledByClientId')
        if not isinstance(owner, str) or not owner:
            raise RuntimeError('Manager task has no available desktop owner')
        return owner

    def wake(self, manager_id, prompt, owner):
        # No model, effort, permission, workspace, or approval overrides.
        reply = self.request('thread-follower-start-turn', {
            'conversationId': manager_id,
            'turnStart': {'request': {'threadId': manager_id,
                                     'input': [{'type': 'text', 'text': prompt, 'text_elements': []}]},
                          'context': {'inheritThreadSettings': True}},
        }, version=2, target=owner)
        # The desktop bridge unwraps the renderer's {method, result} before
        # returning it over IPC. The method belongs to the response envelope,
        # not reply.result. request() has already checked its request ID.
        if (reply.get('resultType') != 'success'
                or reply.get('method') != 'thread-follower-start-turn'
                or reply.get('handledByClientId') != owner
                or not isinstance(reply.get('result'), dict)):
            raise RuntimeError('Unexpected wake response; inspect the manager before retrying')
        return True
