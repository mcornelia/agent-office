#!/usr/bin/env python3
"""Safe lifecycle and login-item controller for the Agent Office desktop app."""

import argparse
import fcntl
import json
import os
import plistlib
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

LABEL = "com.mcornelia.agent-office.launcher"
DEFAULT_PORT = 4318


class ControllerError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def service_url(port, presentation=False):
    suffix = "/presentation" if presentation else "/"
    return f"http://127.0.0.1:{port}{suffix}"


def probe(port, timeout=0.4):
    """Return the Agent Office health record, or None when it is unavailable."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            data = json.loads(response.read(16384))
        if data.get("service") == "agent-office" and data.get("status") == "ok":
            return data
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        pass
    return None


def port_is_open(port, timeout=0.2):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _support_paths(support_dir):
    support_dir = Path(support_dir).expanduser().resolve()
    return {
        "root": support_dir,
        "lock": support_dir / "server.lock",
        "owner": support_dir / "server.json",
        "log": support_dir / "server.log",
        "identities": support_dir / "identities.json",
    }


def _locked(paths):
    paths["root"].mkdir(parents=True, exist_ok=True, mode=0o700)
    paths["root"].chmod(0o700)
    stream = paths["lock"].open("a+")
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    return stream


def _write_owner(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def _read_owner(path):
    try:
        data = json.loads(path.read_text())
        if type(data.get("pid")) is not int or type(data.get("port")) is not int:
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        return None


def _process_command(pid):
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _owned_process_matches(record):
    command = _process_command(record["pid"])
    server = record.get("server")
    if not isinstance(server, str) or not server:
        return False
    expected = str(Path(server).resolve())
    return bool(command and expected and expected in command and "server.py" in command)


def _source_roster(root):
    source_marker = root / "source-root.txt"
    try:
        source_root = Path(source_marker.read_text().strip()).expanduser().resolve()
    except OSError:
        source_root = root
    roster = source_root / "manager" / "team.json"
    return roster if roster.is_file() else None


def ensure_server(root, support_dir, codex_dir, port=DEFAULT_PORT, python="/usr/bin/python3",
                  timeout=6.0, public_hosts=()):
    root = Path(root).expanduser().resolve()
    server = root / "server.py"
    if not server.is_file():
        raise ControllerError(f"Agent Office runtime is incomplete: {server} is missing")
    paths = _support_paths(support_dir)
    with _locked(paths):
        health = probe(port)
        if health:
            return {"running": True, "started": False, "url": service_url(port), "pid": None}
        if port_is_open(port):
            raise ControllerError(f"Port {port} is occupied by a different local service")

        old_owner = _read_owner(paths["owner"])
        if old_owner and _owned_process_matches(old_owner):
            raise ControllerError("An owned Agent Office process exists but its health check failed; inspect the log before restarting")
        paths["owner"].unlink(missing_ok=True)

        command = [
            python,
            str(server),
            "--codex-dir", str(Path(codex_dir).expanduser().resolve()),
            "--port", str(port),
            "--identity-path", str(paths["identities"]),
        ]
        roster = _source_roster(root)
        if roster:
            command.extend(["--roster-path", str(roster)])
        for host in public_hosts:
            command.extend(["--public-host", host])
        try:
            log = paths["log"].open("ab", buffering=0)
            paths["log"].chmod(0o600)
            process = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise ControllerError(f"Could not start Agent Office: {exc}") from exc
        finally:
            if "log" in locals():
                log.close()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if probe(port):
                if process.poll() is None:
                    record = {"pid": process.pid, "port": port, "server": str(server), "startedAt": utc_now()}
                    _write_owner(paths["owner"], record)
                    return {"running": True, "started": True, "url": service_url(port), "pid": process.pid}
                return {"running": True, "started": False, "url": service_url(port), "pid": None}
            status = process.poll()
            if status is not None:
                raise ControllerError(f"Agent Office exited during startup with status {status}; see {paths['log']}")
            time.sleep(0.1)

        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        raise ControllerError(f"Agent Office did not become healthy; see {paths['log']}")


def status(support_dir, port=DEFAULT_PORT):
    paths = _support_paths(support_dir)
    health = probe(port)
    owner = _read_owner(paths["owner"])
    return {
        "running": bool(health),
        "owned": bool(owner and _owned_process_matches(owner)),
        "pid": owner.get("pid") if owner else None,
        "url": service_url(port),
        "log": str(paths["log"]),
    }


def stop_server(support_dir, port=DEFAULT_PORT, timeout=5.0):
    paths = _support_paths(support_dir)
    with _locked(paths):
        owner = _read_owner(paths["owner"])
        if not owner:
            return {"stopped": False, "reason": "No server owned by the desktop launcher"}
        if owner["port"] != port or not _owned_process_matches(owner):
            raise ControllerError("Ownership record does not match the live process; refusing to stop it")
        os.kill(owner["pid"], signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _process_command(owner["pid"]):
                paths["owner"].unlink(missing_ok=True)
                return {"stopped": True, "pid": owner["pid"]}
            time.sleep(0.1)
        raise ControllerError("Agent Office did not stop after SIGTERM; it was left running")


def login_item_record(app_path):
    app_path = Path(app_path).expanduser().resolve()
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/open", "-g", str(app_path), "--args", "--login"],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }


def enable_login_item(launch_agents_dir, app_path):
    app_path = Path(app_path).expanduser().resolve()
    if not app_path.is_dir() or app_path.suffix != ".app":
        raise ControllerError(f"Application bundle not found: {app_path}")
    directory = Path(launch_agents_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{LABEL}.plist"
    expected = login_item_record(app_path)
    if destination.exists():
        try:
            if plistlib.loads(destination.read_bytes()) == expected:
                return {"enabled": True, "changed": False, "path": str(destination)}
        except (OSError, plistlib.InvalidFileException):
            pass
        raise ControllerError(f"A different login item already exists at {destination}; disable it before replacing it")
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(plistlib.dumps(expected, sort_keys=True))
    temporary.chmod(0o600)
    temporary.replace(destination)
    return {"enabled": True, "changed": True, "path": str(destination)}


def disable_login_item(launch_agents_dir):
    destination = Path(launch_agents_dir).expanduser().resolve() / f"{LABEL}.plist"
    changed = destination.exists()
    destination.unlink(missing_ok=True)
    return {"enabled": False, "changed": changed, "path": str(destination)}


def login_item_status(launch_agents_dir, app_path=None):
    destination = Path(launch_agents_dir).expanduser().resolve() / f"{LABEL}.plist"
    if not destination.exists():
        return {"enabled": False, "matches": False, "path": str(destination)}
    try:
        record = plistlib.loads(destination.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return {"enabled": True, "matches": False, "path": str(destination)}
    matches = app_path is None or record == login_item_record(app_path)
    return {"enabled": True, "matches": matches, "path": str(destination)}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--support-dir", type=Path, default=Path.home() / "Library" / "Application Support" / "Agent Office")
    parser.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--launch-agents-dir", type=Path, default=Path.home() / "Library" / "LaunchAgents")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--public-host", action="append", default=[],
                        help="Exact HTTPS proxy Host accepted by the loopback server")
    parser.add_argument("--python", default="/usr/bin/python3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start")
    commands.add_parser("status")
    commands.add_parser("stop")
    url = commands.add_parser("url")
    url.add_argument("--presentation", action="store_true")
    enable = commands.add_parser("enable-login")
    enable.add_argument("--app", required=True, type=Path)
    commands.add_parser("disable-login")
    login_status = commands.add_parser("login-status")
    login_status.add_argument("--app", type=Path)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            result = ensure_server(args.root, args.support_dir, args.codex_dir, args.port,
                                   args.python, public_hosts=args.public_host)
        elif args.command == "status":
            result = status(args.support_dir, args.port)
        elif args.command == "stop":
            result = stop_server(args.support_dir, args.port)
        elif args.command == "url":
            result = {"url": service_url(args.port, args.presentation)}
        elif args.command == "enable-login":
            result = enable_login_item(args.launch_agents_dir, args.app)
        elif args.command == "disable-login":
            result = disable_login_item(args.launch_agents_dir)
        else:
            result = login_item_status(args.launch_agents_dir, args.app)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ControllerError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
