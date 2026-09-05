#!/usr/bin/env python3
"""Read-only, loopback-only companion for local pinned Codex tasks.

Uses observed local file formats, not a supported public desktop API.
Never sends prompts, changes pins, or opens the keyboard.
"""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from desktop_status import DesktopStatus, WAIT_FLAGS
from communications import CommunicationFeed

ROOT = Path(__file__).resolve().parent
MAX_TAIL = 8 * 1024 * 1024
SERVICE_NAME = "agent-office"


def presentation_snapshot(snapshot):
    """Remove task identities and assignment details before serialization."""
    if not snapshot.get("connected"):
        return {
            "connected": False,
            "presentation": True,
            "error": "Activity unavailable",
            "slots": [],
            "communications": [],
        }
    slots = []
    for item in snapshot.get("slots", []):
        occupied = item.get("id") is not None
        key = item.get("key")
        slots.append({
            "key": key,
            "id": f"presentation-slot-{key}" if occupied else None,
            "title": f"Agent {key}" if occupied else "Unassigned",
            "avatar": item.get("avatar"),
            "state": item.get("state"),
            "eventAt": item.get("eventAt"),
            "approvalStateAvailable": bool(item.get("approvalStateAvailable")),
        })
    return {
        "connected": True,
        "presentation": True,
        "source": "local activity",
        "selectionAvailable": False,
        "approvalStateAvailable": bool(snapshot.get("approvalStateAvailable")),
        "observedAt": snapshot.get("observedAt"),
        "slots": slots,
        # Communication events contain stable task IDs, so presentation mode
        # suppresses the event layer rather than attempting to pseudonymize it.
        "communications": [],
    }


class EventTail:
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.inode = None
        self.pending = b""
        self.state = "unknown"
        self.event_at = None

    def read(self):
        stat = self.path.stat()
        if self.inode != stat.st_ino or stat.st_size < self.offset:
            self.inode = stat.st_ino
            self.offset = max(0, stat.st_size - MAX_TAIL)
            self.pending = b""
            self.state = "unknown"
            self.event_at = None
            skip_first = self.offset > 0
        else:
            skip_first = False
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            chunk = stream.read(MAX_TAIL)
            self.offset = stream.tell()
        if skip_first:
            chunk = chunk.partition(b"\n")[2]
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        for line in lines:
            try:
                item = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if item.get("type") != "event_msg":
                continue
            payload = item.get("payload", {})
            event = payload.get("type")
            states = {"task_started": "working", "task_complete": "complete", "turn_aborted": "interrupted", "error": "error"}
            if event in states:
                self.state = states[event]
                self.event_at = item.get("timestamp")
        # A quiet or abandoned old run must not remain blue indefinitely.
        state = self.state
        if state == "working" and time.time() - stat.st_mtime > 300:
            state = "unknown"
        return state, self.event_at


class OfficeState:
    def __init__(self, codex_dir, identity_path=None, desktop_status=None, roster_path=None):
        self.codex_dir = Path(codex_dir).expanduser().resolve()
        self.identity_path = identity_path
        self.identities = {}
        self.tails = {}
        self.lock = Lock()
        self.desktop_status = desktop_status
        self.communications = CommunicationFeed(self.codex_dir, roster_path or ROOT / 'manager' / 'team.json')
        if identity_path and identity_path.exists():
            try:
                data = json.loads(identity_path.read_text())
                self.identities = {k: v for k, v in data.items() if isinstance(k, str) and type(v) is int and 0 <= v < 6}
            except (ValueError, OSError):
                pass

    def connect(self):
        candidates = list(self.codex_dir.glob("state_*.sqlite"))
        if not candidates:
            raise RuntimeError("No local Codex task database found")
        db = max(candidates, key=lambda p: int(p.stem.split("_")[-1]))
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con

    def app_state(self):
        path = self.codex_dir / ".codex-global-state.json"
        return json.loads(path.read_text())

    def pinned(self, con, app):
        columns = {row[1] for row in con.execute('PRAGMA table_info(threads)')}
        title = "COALESCE(NULLIF(name,''),title) AS title" if 'name' in columns else 'title'
        # Current desktop builds store the pinned section and its order here.
        sections = con.execute("SELECT id FROM thread_sections WHERE name='Pinned'").fetchall()
        if len(sections) == 1:
            return con.execute(f"SELECT id,{title},rollout_path FROM threads WHERE thread_section_id=? AND archived=0 ORDER BY section_position ASC,id ASC LIMIT 6", (sections[0]["id"],)).fetchall()
        # Legacy builds used an ordered list in the desktop state file.
        ids = app.get("pinned-thread-ids", [])[:6]
        rows = []
        for thread_id in ids:
            if not isinstance(thread_id, str):
                raise RuntimeError("Unsupported pinned-task format")
            row = con.execute(f"SELECT id,{title},rollout_path FROM threads WHERE id=? AND archived=0", (thread_id,)).fetchone()
            if row is None:
                raise RuntimeError("A pinned task is not available in this Mac's local task database")
            rows.append(row)
        return rows

    def observed(self, row):
        path = Path(row["rollout_path"]).resolve()
        try:
            path.relative_to(self.codex_dir / "sessions")
        except ValueError:
            raise RuntimeError("Task history is outside the local sessions directory")
        key = (row["id"], str(path))
        tail = self.tails.setdefault(key, EventTail(path))
        return tail.read()

    def snapshot(self, presentation=False):
        with self.lock:
            try:
                app = self.app_state()
                con = self.connect()
                try:
                    rows = self.pinned(con, app)
                finally:
                    con.close()
                if self.desktop_status:
                    self.desktop_status.follow(row['id'] for row in rows)
                unread = set(app.get("electron-persisted-atom-state", {}).get("unread-thread-ids-by-host-v1", {}).get("local", []))
                used = set()
                assigned = {}
                for row in rows:
                    avatar = self.identities.get(row["id"])
                    if avatar is not None and avatar not in used:
                        assigned[row["id"]] = avatar
                        used.add(avatar)
                for row in rows:
                    if row["id"] not in assigned:
                        avatar = next(i for i in range(6) if i not in used)
                        assigned[row["id"]] = avatar
                        used.add(avatar)
                changed = any(self.identities.get(k) != v for k, v in assigned.items())
                self.identities.update(assigned)
                if changed and self.identity_path:
                    temporary = self.identity_path.with_suffix(".tmp")
                    temporary.write_text(json.dumps(self.identities, indent=2) + "\n")
                    temporary.chmod(0o600)
                    temporary.replace(self.identity_path)
                slots = []
                for i, row in enumerate(rows):
                    try:
                        observed, event_at = self.observed(row)
                        state = "done" if observed == "complete" and row["id"] in unread else "idle" if observed in ("complete", "interrupted") else observed
                    except (OSError, RuntimeError):
                        state, event_at = "unknown", None
                    runtime = self.desktop_status.get(row['id']) if self.desktop_status else None
                    if runtime:
                        if runtime['type'] == 'active':
                            state = 'waiting' if WAIT_FLAGS.intersection(runtime['activeFlags']) else 'working'
                        elif runtime['type'] == 'systemError':
                            state = 'error'
                        elif runtime['type'] == 'idle':
                            state = 'done' if row['id'] in unread else 'idle'
                    elif self.desktop_status and state == 'working':
                        state = 'unknown'
                    slots.append({"key": i+1, "id": row["id"], "title": row["title"], "avatar": assigned[row["id"]], "state": state, "eventAt": event_at, "approvalStateAvailable": runtime is not None})
                for i in range(len(rows), 6):
                    avatar = next(n for n in range(6) if n not in used)
                    used.add(avatar)
                    slots.append({"key": i+1, "id": None, "title": "No pinned task", "avatar": avatar, "state": "unassigned", "eventAt": None})
                # Drop unused file readers; no transcript data is returned.
                current_ids = {r["id"] for r in rows}
                self.tails = {k: v for k, v in self.tails.items() if k[0] in current_ids}
                result = {"connected": True, "source": "local Codex task events and desktop status", "selectionAvailable": False, "approvalStateAvailable": any(s.get('approvalStateAvailable') for s in slots), "observedAt": datetime.now(timezone.utc).isoformat(), "slots": slots, "communications": self.communications.snapshot(rows)}
            except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
                result = {"connected": False, "error": str(exc), "slots": []}
            return presentation_snapshot(result) if presentation else result


def make_handler(state, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin not in {f"http://{h}" for h in allowed}):
                self.send_error(403)
                return
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            if request.path == "/api/state":
                data = json.dumps(state.snapshot(presentation=query.get("presentation") == ["1"])).encode()
                mime = "application/json"
            elif request.path == "/api/health":
                data = json.dumps({"status": "ok", "service": SERVICE_NAME, "presentationSupported": True}).encode()
                mime = "application/json"
            elif request.path in ("/", "/index.html", "/presentation"):
                data = (ROOT / "index.html").read_bytes()
                mime = "text/html; charset=utf-8"
            elif request.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--identity-path", type=Path, help="Private writable task-to-character assignment file")
    parser.add_argument("--roster-path", type=Path, help="Optional private manager roster used for generic activity bubbles")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--inspect-thread", help="Read one local task's lifecycle for validation, without changing its pins")
    args = parser.parse_args()
    desktop = DesktopStatus(args.codex_dir.expanduser().resolve())
    identity_path = args.identity_path.expanduser().resolve() if args.identity_path else ROOT / "identities.json"
    roster_path = args.roster_path.expanduser().resolve() if args.roster_path else None
    state = OfficeState(args.codex_dir, None if args.check or args.inspect_thread else identity_path, desktop, roster_path)
    if args.inspect_thread:
        con = state.connect()
        try:
            row = con.execute("SELECT id,title,rollout_path FROM threads WHERE id=?", (args.inspect_thread,)).fetchone()
        finally:
            con.close()
        if row is None:
            parser.error("Task not found on this Mac")
        observed, event_at = state.observed(row)
        print(json.dumps({"id": row["id"], "state": observed, "eventAt": event_at}, indent=2))
        return
    if args.check:
        desktop.start()
        state.snapshot()
        time.sleep(0.75)
        print(json.dumps(state.snapshot(), indent=2))
        desktop.close()
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state, args.port))
    desktop.start()
    print(f"Agent Office: http://127.0.0.1:{args.port} — Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        desktop.close()
        server.server_close()


if __name__ == "__main__":
    main()
