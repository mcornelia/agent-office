import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name('agent_office_ctl.py')
SPEC = importlib.util.spec_from_file_location('agent_office_ctl', MODULE_PATH)
ctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ctl)


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit):
        return self.body


class DesktopControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_probe_recognizes_only_agent_office(self):
        valid = Response(b'{"status":"ok","service":"agent-office","presentationSupported":true}')
        with mock.patch.object(ctl, 'urlopen', return_value=valid) as request:
            self.assertEqual(ctl.probe(4318)['service'], 'agent-office')
            self.assertIn('/api/health', request.call_args.args[0])
        foreign = Response(b'{"status":"ok","service":"something-else"}')
        with mock.patch.object(ctl, 'urlopen', return_value=foreign):
            self.assertIsNone(ctl.probe(4318))

    def test_service_urls_are_loopback_only(self):
        self.assertEqual(ctl.service_url(4318), 'http://127.0.0.1:4318/')
        self.assertEqual(ctl.service_url(4318, True), 'http://127.0.0.1:4318/presentation')

    def test_healthy_existing_server_is_reused_without_launching(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'server.py').write_text('# test runtime\n')
        with mock.patch.object(ctl, 'probe', return_value={'status': 'ok', 'service': 'agent-office'}), \
             mock.patch.object(ctl.subprocess, 'Popen') as launch:
            result = ctl.ensure_server(runtime, self.root / 'support', self.root / 'codex')
        self.assertTrue(result['running'])
        self.assertFalse(result['started'])
        launch.assert_not_called()

    def test_foreign_service_on_port_fails_closed(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'server.py').write_text('# test runtime\n')
        with mock.patch.object(ctl, 'probe', return_value=None), \
             mock.patch.object(ctl, 'port_is_open', return_value=True), \
             mock.patch.object(ctl.subprocess, 'Popen') as launch:
            with self.assertRaises(ctl.ControllerError):
                ctl.ensure_server(runtime, self.root / 'support', self.root / 'codex')
        launch.assert_not_called()

    def test_login_item_is_opt_in_idempotent_and_reversible(self):
        app = self.root / 'Agent Office.app'
        app.mkdir()
        agents = self.root / 'LaunchAgents'
        first = ctl.enable_login_item(agents, app)
        second = ctl.enable_login_item(agents, app)
        self.assertTrue(first['changed'])
        self.assertFalse(second['changed'])
        record = plistlib.loads(Path(first['path']).read_bytes())
        self.assertEqual(record['Label'], ctl.LABEL)
        self.assertEqual(record['ProgramArguments'][-1], '--login')
        self.assertTrue(ctl.login_item_status(agents, app)['matches'])
        removed = ctl.disable_login_item(agents)
        self.assertTrue(removed['changed'])
        self.assertFalse(ctl.login_item_status(agents, app)['enabled'])

    def test_login_item_refuses_to_overwrite_a_different_record(self):
        app = self.root / 'Agent Office.app'
        other = self.root / 'Other.app'
        app.mkdir()
        other.mkdir()
        agents = self.root / 'LaunchAgents'
        ctl.enable_login_item(agents, app)
        with self.assertRaises(ctl.ControllerError):
            ctl.enable_login_item(agents, other)

    def test_invalid_app_is_not_enabled(self):
        with self.assertRaises(ctl.ControllerError):
            ctl.enable_login_item(self.root / 'LaunchAgents', self.root / 'missing.app')


if __name__ == '__main__':
    unittest.main()
