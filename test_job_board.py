import copy
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from job_board import JobBoardFeed, MAX_SOURCE_BYTES, public_text

CONTRACT = json.loads((Path(__file__).parent / 'docs' / 'job-board.schema.json').read_text())


def assert_contract(test, value, schema=CONTRACT):
    """Check every keyword used by our small schema, without a test dependency.

    This is deliberately not a general JSON Schema implementation. Fail if the
    contract adds a keyword this checker does not yet implement.
    """
    supported = {'$schema', 'title', '$defs', '$ref', 'anyOf', 'type', 'const',
                 'enum', 'required', 'additionalProperties', 'properties',
                 'items', 'maxItems', 'minItems', 'uniqueItems', 'maxLength',
                 'pattern', 'format'}
    test.assertFalse(set(schema) - supported, 'Extend the contract checker for new schema keywords')
    if '$ref' in schema:
        assert_contract(test, value, CONTRACT['$defs'][schema['$ref'].removeprefix('#/$defs/')])
    if 'anyOf' in schema:
        for variant in schema['anyOf']:
            try:
                assert_contract(test, value, variant)
                break
            except AssertionError:
                continue
        else:
            test.fail('No contract variant matched')
    if 'type' in schema:
        allowed = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        types = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}
        test.assertIn(type(value), [types[t] for t in allowed])
    if 'const' in schema:
        test.assertEqual(type(value), type(schema['const']))
        test.assertEqual(value, schema['const'])
    if 'enum' in schema:
        test.assertTrue(any(type(value) is type(v) and value == v for v in schema['enum']))
    if isinstance(value, dict) and 'properties' in schema:
        test.assertTrue(set(schema.get('required', [])) <= set(value))
        if schema.get('additionalProperties') is False:
            test.assertFalse(set(value) - set(schema['properties']))
        for key in value.keys() & schema['properties'].keys():
            assert_contract(test, value[key], schema['properties'][key])
    if isinstance(value, list):
        test.assertLessEqual(len(value), schema.get('maxItems', len(value)))
        test.assertGreaterEqual(len(value), schema.get('minItems', 0))
        if schema.get('uniqueItems'):
            test.assertEqual(len(value), len({json.dumps(v, sort_keys=True) for v in value}))
        if 'items' in schema:
            for item in value:
                assert_contract(test, item, schema['items'])
    if isinstance(value, str):
        test.assertLessEqual(len(value), schema.get('maxLength', len(value)))
        if 'pattern' in schema:
            test.assertIsNotNone(re.search(schema['pattern'], value))
        if schema.get('format') == 'date-time':
            test.assertIsNotNone(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo)


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


class JobBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.roster_path = self.root / 'team.json'
        self.ledger_path = self.root / 'state.json'
        self.worker = 'worker-private-id'
        self.manager = 'manager-private-id'
        self.roster = {'schemaVersion': 1, 'managerThreadId': self.manager, 'members': [
            {'threadId': self.manager, 'titleAtSetup': 'PRIVATE MANAGER TITLE',
             'presentation': {'label': 'Scout'}},
            {'threadId': self.worker, 'titleAtSetup': 'PRIVATE WORKER TITLE',
             'presentation': {'label': 'Nova'}},
        ]}
        self.job = {'taskId': self.worker, 'sourceTurnId': 'private-turn-id',
                    'sourceTimestamp': iso(900), 'lastObservedStatus': 'building',
                    'objective': 'PRIVATE OBJECTIVE /Users/person/secret',
                    'evidence': 'PRIVATE RESULT TEXT', 'nextStep': 'PRIVATE NEXT STEP',
                    'cursor': 'private-cursor', 'lastFollowUpSignature': 'private-followup',
                    'lastReportedSignature': 'private-report', 'blocker': None}
        self.ledger = {'schemaVersion': 1, 'lastCheckAt': iso(1000),
                       'workers': {self.worker: {'tracking': 'registered_job'}},
                       'jobs': [copy.deepcopy(self.job)]}
        self.slots = [{'key': 1, 'id': self.manager, 'avatar': 0, 'title': 'PRIVATE TITLE'},
                      {'key': 2, 'id': self.worker, 'avatar': 4, 'rollout_path': '/private/path'}]
        self.write()
        self.feed = JobBoardFeed(self.roster_path, stale_after=100)

    def write(self):
        self.roster_path.write_text(json.dumps(self.roster))
        self.ledger_path.write_text(json.dumps(self.ledger))

    def snapshot(self, at=1001, runtime=None):
        result = self.feed.snapshot(self.slots, runtime, now=at)
        assert_contract(self, result)
        return result

    def worker_agent(self, snap):
        return next(a for a in snap['agents'] if a['label'] == 'Nova')

    def update(self, at=1010, **fields):
        self.ledger['lastCheckAt'] = iso(at)
        self.ledger['jobs'][0].update(fields)
        self.write()

    def test_explicit_stage_and_stable_identity_survive_reorder(self):
        first = self.worker_agent(self.snapshot())
        self.assertEqual(first['assignment']['stage'], 'building')
        self.assertEqual(first['key'], 2)
        self.slots[0]['key'], self.slots[1]['key'] = 2, 1
        second = self.worker_agent(self.snapshot())
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['avatar'], 4)
        self.assertEqual(second['key'], 1)

    def test_private_fields_never_reach_presentation(self):
        self.update(blocker={'details': 'PRIVATE BLOCKER /Users/person/secret'})
        raw = json.dumps(self.snapshot(at=1011))
        for token in ['PRIVATE', '/Users/', '/private/', self.worker, self.manager,
                      'private-turn-id', 'private-cursor', 'private-followup', 'private-report',
                      'rollout_path', 'sourceTurnId', 'objective', 'evidence', 'nextStep']:
            self.assertNotIn(token, raw)

    def test_unknown_stage_is_not_inferred_from_objective_or_runtime(self):
        self.update(lastObservedStatus='active', objective='researching building testing')
        snap = self.snapshot(at=1011, runtime={self.worker: {'type': 'active', 'activeFlags': []}})
        self.assertEqual(self.worker_agent(snap)['assignment']['stage'], 'unknown')

    def test_researching_testing_and_waiting_are_explicit_stages(self):
        for stage in ('researching', 'testing', 'waiting'):
            with self.subTest(stage=stage):
                self.update(lastObservedStatus=stage)
                snap = self.snapshot(at=1011)
                self.assertEqual(self.worker_agent(snap)['assignment']['stage'], stage)
                self.assertEqual(snap['needsYou'], [])  # waiting alone is not an actionable ask

    def test_baseline_worker_history_does_not_create_assignments(self):
        self.ledger['jobs'] = []
        self.ledger['workers'][self.worker] = {'tracking': 'baseline_only', 'lastObservedStatus': 'active',
                                              'title': 'PRIVATE OLD ASSIGNMENT'}
        self.write()
        snap = self.snapshot()
        self.assertIsNone(self.worker_agent(snap)['assignment'])
        self.assertEqual(snap['results'], [])

    def test_idle_desktop_does_not_prove_job_completion(self):
        snap = self.snapshot(runtime={self.worker: {'type': 'idle'}})
        self.assertEqual(snap['results'], [])
        self.assertEqual(self.worker_agent(snap)['assignment']['status'], 'active')

    def test_stale_work_stage_is_unknown(self):
        snap = self.snapshot(at=1101)
        self.assertTrue(snap['source']['stale'])
        self.assertTrue(self.worker_agent(snap)['assignment']['stale'])
        self.assertEqual(self.worker_agent(snap)['assignment']['stage'], 'unknown')

    def test_old_job_is_stale_even_with_fresh_manager_check(self):
        self.update(updatedAt=iso(500))
        snap = self.snapshot(at=1011)
        self.assertFalse(snap['source']['stale'])
        self.assertTrue(self.worker_agent(snap)['assignment']['stale'])

    def test_waiting_persists_through_age_omission_and_source_failure(self):
        self.update(lastObservedStatus='awaiting_approval')
        first = self.snapshot(at=1011)['needsYou'][0]
        self.assertEqual(first['kind'], 'approval')
        self.assertEqual(self.worker_agent(self.snapshot(at=1500))['assignment']['stage'], 'waiting')
        self.ledger['jobs'] = []
        self.ledger['lastCheckAt'] = iso(1600)
        self.write()
        self.assertEqual(self.snapshot(at=1601)['needsYou'][0]['id'], first['id'])
        self.ledger_path.write_text('{partial')
        failed = self.snapshot(at=2000)
        self.assertFalse(failed['source']['available'])
        self.assertEqual(failed['source']['issues'], ['ledger_unavailable'])
        self.assertEqual(failed['needsYou'][0]['firstObservedAt'], first['firstObservedAt'])
        self.assertTrue(failed['needsYou'][0]['stale'])

    def test_later_explicit_resolution_clears_same_job_only(self):
        self.update(lastObservedStatus='awaiting_user_review')
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='testing', blocker=None)
        self.assertEqual(self.snapshot(at=1021)['needsYou'], [])

    def test_verified_reconciliation_clears_staleness_and_resolved_need(self):
        self.update(lastObservedStatus='awaiting_user_review', updatedAt=iso(1010),
                    blocker='PRIVATE REVIEW BLOCKER')
        stale=self.snapshot(at=1121)
        self.assertTrue(stale['source']['stale'])
        self.assertTrue(stale['needsYou'][0]['stale'])
        self.update(at=1130, lastObservedStatus='testing', updatedAt=iso(1130), blocker=None)
        refreshed=self.snapshot(at=1131)
        self.assertFalse(refreshed['source']['stale'])
        self.assertEqual(refreshed['needsYou'], [])
        assignment=self.worker_agent(refreshed)['assignment']
        self.assertEqual(assignment['stage'], 'testing')
        self.assertFalse(assignment['stale'])

    def test_missing_blocker_field_is_not_resolution(self):
        self.update(lastObservedStatus='blocked', blocker='PRIVATE BLOCKER')
        first = self.snapshot(at=1011)['needsYou'][0]
        del self.ledger['jobs'][0]['blocker']
        self.ledger['jobs'][0]['lastObservedStatus'] = 'unknown'
        self.ledger['lastCheckAt'] = iso(1020)
        self.write()
        self.assertEqual(self.snapshot(at=1021)['needsYou'][0]['id'], first['id'])

    def test_new_job_does_not_resolve_old_job_request(self):
        self.update(lastObservedStatus='awaiting_approval')
        first = self.snapshot(at=1011)['needsYou'][0]
        self.update(at=1020, sourceTurnId='another-private-turn', lastObservedStatus='completed')
        result = self.snapshot(at=1021)
        self.assertEqual(result['needsYou'][0]['id'], first['id'])
        self.assertEqual(len(result['results']), 1)

    def test_old_job_request_does_not_change_new_assignment_stage(self):
        self.update(lastObservedStatus='awaiting_approval')
        first = self.snapshot(at=1011)['needsYou'][0]
        self.update(at=1020, sourceTurnId='new-turn', lastObservedStatus='building')
        snap = self.snapshot(at=1021)
        self.assertEqual(snap['needsYou'][0]['id'], first['id'])
        self.assertEqual(self.worker_agent(snap)['assignment']['stage'], 'building')

    def test_completed_job_clears_pending_request_and_creates_result(self):
        self.update(lastObservedStatus='awaiting_user_review')
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='completed')
        snap = self.snapshot(at=1021)
        self.assertEqual(snap['needsYou'], [])
        self.assertEqual(len(snap['results']), 1)
        self.assertNotIn('PRIVATE', json.dumps(snap['results']))
        self.assertIsNone(self.worker_agent(snap)['assignment'])

    def test_desktop_wait_persists_without_authoritative_status(self):
        waiting = {self.worker: {'type': 'active', 'activeFlags': ['waitingOnApproval']}}
        first = self.snapshot(runtime=waiting)['needsYou'][0]
        for runtime in (None, {}, {self.worker: None}, {self.worker: {'type': 'notLoaded'}},
                        {self.worker: {'type': 'active'}},
                        {self.worker: {'type': 'active', 'activeFlags': [None]}},
                        {self.worker: {'type': 'active', 'activeFlags': None}}):
            request = self.snapshot(at=1002, runtime=runtime)['needsYou'][0]
            self.assertEqual(request['id'], first['id'])
            self.assertTrue(request['stale'])
        self.assertEqual(self.snapshot(runtime={self.worker: {'type': 'active', 'activeFlags': []}})['needsYou'], [])

    def test_runtime_wait_kind_transition_replaces_not_duplicates(self):
        self.snapshot(runtime={self.worker: {'type': 'active', 'activeFlags': ['waitingOnApproval']}})
        snap = self.snapshot(runtime={self.worker: {'type': 'active', 'activeFlags': ['waitingOnUserInput']}})
        self.assertEqual(len(snap['needsYou']), 1)
        self.assertEqual(snap['needsYou'][0]['kind'], 'input')
        self.assertEqual(self.snapshot(runtime={self.worker: {'type': 'idle'}})['needsYou'], [])

    def test_ledger_and_runtime_share_one_request_with_two_sources(self):
        self.update(lastObservedStatus='awaiting_approval')
        runtime = {self.worker: {'type': 'active', 'activeFlags': ['waitingOnApproval']}}
        snap = self.snapshot(at=1011, runtime=runtime)
        self.assertEqual(len(snap['needsYou']), 1)
        self.assertEqual(snap['needsYou'][0]['sources'], ['desktop_status', 'manager_ledger'])
        # Clearing only the live flag must not approve an unresolved ledger ask.
        self.assertEqual(len(self.snapshot(at=1012, runtime={self.worker: {'type': 'idle'}})['needsYou']), 1)
        self.update(at=1020, lastObservedStatus='completed')
        self.assertEqual(self.snapshot(at=1021)['needsYou'], [])

    def test_pending_ledger_request_reconstructs_after_restart(self):
        self.update(lastObservedStatus='awaiting_approval')
        first = self.snapshot(at=1011)['needsYou'][0]
        other = JobBoardFeed(self.roster_path)
        second = other.snapshot(self.slots, now=1012)['needsYou'][0]
        self.assertEqual(first['id'], second['id'])

    def test_live_wait_preserves_explicit_safe_manager_action(self):
        self.update(lastObservedStatus='awaiting_user_review', presentation={
            'needTitle': 'Choose the layout', 'action': 'Choose compact or expanded.'})
        snap = self.snapshot(at=1011, runtime={self.worker: {'type': 'active', 'activeFlags': ['waitingOnUserInput']}})
        self.assertEqual(snap['needsYou'][0]['title'], 'Choose the layout')
        self.assertEqual(snap['needsYou'][0]['action'], 'Choose compact or expanded.')

    def test_both_request_sources_report_missing_runtime_as_stale(self):
        self.update(lastObservedStatus='awaiting_approval')
        self.snapshot(at=1011, runtime={self.worker: {'type': 'active', 'activeFlags': ['waitingOnApproval']}})
        self.assertTrue(self.snapshot(at=1012)['needsYou'][0]['stale'])

    def test_duplicates_keep_newest_job_evidence_and_one_result(self):
        old = dict(self.job, lastObservedStatus='building', updatedAt=iso(900))
        new = dict(self.job, lastObservedStatus='completed', updatedAt=iso(990))
        self.ledger['jobs'] = [new, old, new]
        self.write()
        snap = self.snapshot()
        self.assertEqual(len(snap['results']), 1)
        identifier = snap['results'][0]['id']
        self.ledger['jobs'][0]['objective'] = 'Different private wording'
        self.write()
        self.assertEqual(self.snapshot()['results'][0]['id'], identifier)

    def test_results_sorted_newest_first_and_retained_on_ledger_compaction(self):
        self.ledger['jobs'] = [dict(self.job, sourceTurnId='old', lastObservedStatus='completed', completedAt=iso(900)),
                               dict(self.job, sourceTurnId='new', lastObservedStatus='completed', completedAt=iso(999))]
        self.write()
        first = self.snapshot()['results']
        self.assertEqual([r['completedAt'] for r in first], [iso(999), iso(900)])
        self.ledger['jobs'] = []
        self.write()
        self.assertEqual(self.snapshot()['results'], first)

    def test_reopened_job_retracts_previous_completion(self):
        self.update(lastObservedStatus='completed')
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='testing')
        self.assertEqual(self.snapshot(at=1021)['results'], [])

    def test_public_copy_opt_in_is_sanitized(self):
        self.update(lastObservedStatus='completed', presentation={
            'title': 'Review /Users/alice/secret.txt for alice@example.com',
            'summary': 'Ready <script>alert(1)</script> token=hidden ' + self.worker,
        })
        result = self.snapshot(at=1011)['results'][0]
        raw = json.dumps(result)
        for private in ('/Users/', 'alice@example.com', 'hidden', self.worker, '<script>'):
            self.assertNotIn(private, raw)
        self.assertIn('Review', result['title'])

    def test_alternate_job_and_roster_identifiers_are_redacted(self):
        self.roster['members'][1]['presentation']['label'] = 'Nova ' + self.manager
        self.update(id='explicit-private-job', lastObservedStatus='completed', presentation={
            'title': 'Ready ' + self.job['sourceTurnId'],
            'summary': self.worker + ' ' + self.manager + ' explicit-private-job',
            'artifacts': [{'url': 'https://example.com/' + self.job['sourceTurnId'], 'public': True}]})
        snap = self.snapshot(at=1011)
        raw = json.dumps(snap)
        for token in (self.worker, self.manager, 'explicit-private-job', self.job['sourceTurnId']):
            self.assertNotIn(token, raw)
        self.assertIsNone(snap['results'][0]['artifacts'][0]['href'])

    def artifact_snapshot(self, artifacts, *, local=False, roots=()):
        self.feed = JobBoardFeed(self.roster_path, presentation_mode=not local, artifact_roots=roots)
        self.update(lastObservedStatus='completed', presentation={'artifacts': artifacts})
        return self.snapshot(at=1011)['results'][0]['artifacts']

    def test_explicit_external_artifacts_are_deduplicated_and_unverified(self):
        artifact = {'url': 'https://github.com/example/project/pull/7', 'label': 'Review changes', 'public': True}
        links = self.artifact_snapshot([artifact, artifact, {'url': 'https://example.com/private'}])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['href'], artifact['url'])
        self.assertEqual(links[0]['status'], 'unverified')

    def test_unsafe_external_links_do_not_expose_href(self):
        urls = ['javascript:alert(1)', 'data:text/plain,private', 'file:///Users/private',
                'http://127.0.0.1/private', 'http://127.1/private', 'http://office.local/report',
                'http://localhost/report', 'https://user:password@example.com/',
                'https://example.com/?token=secret', 'https://example.com/#private',
                'https://example.com/%2e%2e/.env', 'https://example.com/%252e%252e/private',
                'https://example.com/Users/person/secret', 'https://example.com/%00',
                'https://example.com:9999/report', 'https://bad..example.com/report',
                'https://example.com/' + self.worker]
        artifacts = self.artifact_snapshot([{'url': u, 'public': True} for u in urls])
        self.assertEqual(len(artifacts), len(urls))
        self.assertTrue(all(a['href'] is None and a['status'] == 'unsafe' for a in artifacts))
        self.assertNotIn('password', json.dumps(artifacts))

    def test_local_artifacts_hidden_in_presentation_mode(self):
        path = self.root / 'result.txt'
        path.write_text('contents never read by the board')
        links = self.artifact_snapshot([{'path': str(path), 'public': True}], roots=[self.root])
        self.assertEqual(links[0]['status'], 'hidden')
        self.assertIsNone(links[0]['href'])
        self.assertNotIn(str(self.root), json.dumps(links))

    def test_local_mode_requires_approved_root_and_existing_safe_file(self):
        exports = self.root / 'exports'
        exports.mkdir()
        okay = exports / 'report.pdf'
        okay.write_bytes(b'%PDF fixture')
        outside = self.root / 'private.txt'
        outside.write_text('PRIVATE')
        (exports / 'escape.txt').symlink_to(outside)
        (exports / '.env').write_text('secret')
        (exports / 'active.html').write_text('<script>bad()</script>')
        paths = [okay, exports / 'missing.txt', outside, exports / 'escape.txt', exports / '.env', exports / 'active.html']
        links = self.artifact_snapshot([{'path': str(p), 'public': True} for p in paths], local=True, roots=[exports])
        self.assertEqual([a['status'] for a in links], ['available', 'missing', 'unsafe', 'unsafe', 'unsafe', 'unsafe'])
        self.assertEqual(links[0]['href'], okay.resolve().as_uri())
        okay.unlink()
        self.assertEqual(self.snapshot(at=1012)['results'][0]['artifacts'][0]['status'], 'missing')

    def test_cached_local_link_is_rechecked_after_source_loss(self):
        exports = self.root / 'exports'
        exports.mkdir()
        output = exports / 'report.txt'
        output.write_text('result')
        outside = self.root / 'outside.txt'
        outside.write_text('PRIVATE')
        self.artifact_snapshot([{'path': str(output), 'public': True}], local=True, roots=[exports])
        self.ledger_path.unlink()
        output.unlink()
        output.symlink_to(outside)
        artifact = self.snapshot(at=1012)['results'][0]['artifacts'][0]
        self.assertIsNone(artifact['href'])
        self.assertEqual(artifact['status'], 'unsafe')

    def test_runtime_error_is_not_presented_as_ongoing_build(self):
        snap = self.snapshot(runtime={self.worker: {'type': 'systemError'}})
        self.assertEqual(self.worker_agent(snap)['assignment']['status'], 'error')
        self.assertEqual(self.worker_agent(snap)['assignment']['stage'], 'unknown')

    def test_duplicate_blocker_and_live_approval_are_one_actionable_item(self):
        self.update(lastObservedStatus='blocked', blocker='PRIVATE DETAILS')
        snap = self.snapshot(at=1011, runtime={self.worker: {'type': 'active', 'activeFlags': ['waitingOnApproval']}})
        self.assertEqual(len(snap['needsYou']), 1)
        self.assertEqual(snap['needsYou'][0]['kind'], 'approval')
        self.assertEqual(len(snap['needsYou'][0]['sources']), 2)

    def test_unregistered_remote_and_identityless_jobs_are_ignored(self):
        self.ledger['jobs'] = [dict(self.job, taskId='outsider'), {'taskId': self.worker, 'objective': 'PRIVATE'}]
        self.write()
        self.assertIsNone(self.worker_agent(self.snapshot())['assignment'])
        self.assertEqual(self.snapshot()['results'], [])

    def test_source_failures_are_generic_and_do_not_write(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.roster_path, self.ledger_path)}
        with patch('pathlib.Path.write_text', side_effect=AssertionError('read-only feed wrote a file')):
            self.snapshot()
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.roster_path, self.ledger_path)}
        self.assertEqual(before, after)
        self.ledger_path.unlink()
        failed = self.snapshot()
        self.assertEqual(failed['source']['issues'], ['ledger_unavailable'])
        self.assertNotIn(str(self.root), json.dumps(failed))

    def test_out_of_order_source_cannot_undo_request_resolution(self):
        self.update(lastObservedStatus='awaiting_approval')
        previous = copy.deepcopy(self.ledger)
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='completed')
        self.assertEqual(self.snapshot(at=1021)['needsYou'], [])
        self.ledger = previous
        self.write()
        snap = self.snapshot(at=1022)
        self.assertEqual(snap['needsYou'], [])
        self.assertTrue(snap['source']['stale'])
        self.assertEqual(len(snap['results']), 1)

    def test_older_job_in_newer_ledger_cannot_undo_resolution(self):
        self.update(lastObservedStatus='completed', updatedAt=iso(1010))
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='awaiting_approval', updatedAt=iso(1000))
        snap = self.snapshot(at=1021)
        self.assertEqual(snap['needsYou'], [])
        self.assertEqual(len(snap['results']), 1)
        self.assertEqual(snap['source']['issues'], ['ledger_unavailable'])

    def test_invalid_clearance_does_not_dismiss_request(self):
        self.update(lastObservedStatus='awaiting_approval')
        self.snapshot(at=1011)
        self.update(at=1020, lastObservedStatus='unexpected_state', blocker=None, resolvedAt='not-a-date')
        self.assertEqual(len(self.snapshot(at=1021)['needsYou']), 1)
        self.update(at=1030, resolvedAt=iso(1030))
        snap = self.snapshot(at=1031)
        self.assertEqual(snap['needsYou'], [])
        self.assertIsNone(self.worker_agent(snap)['assignment'])

    def test_future_job_and_resolution_fail_closed(self):
        self.update(lastObservedStatus='awaiting_approval')
        self.snapshot(at=1011)
        for field in ('updatedAt', 'resolvedAt'):
            with self.subTest(field=field):
                self.update(at=1020, **{field: iso(9000)})
                snap = self.snapshot(at=1021)
                self.assertEqual(len(snap['needsYou']), 1)
                self.assertEqual(snap['source']['issues'], ['ledger_unavailable'])
                del self.ledger['jobs'][0][field]

    def test_epoch_zero_timestamps_remain_explicit(self):
        self.update(lastObservedStatus='completed', completedAt=0, updatedAt=0)
        snap = self.snapshot(at=1011)
        self.assertEqual(snap['results'][0]['completedAt'], iso(0))

    def test_results_shelf_is_bounded_and_newest_first(self):
        self.ledger['jobs'] = [dict(self.job, sourceTurnId=str(i), lastObservedStatus='completed', completedAt=iso(i))
                               for i in range(105)]
        self.write()
        results = self.snapshot()['results']
        self.assertEqual(len(results), 100)
        self.assertEqual(results[0]['completedAt'], iso(104))
        self.assertEqual(results[-1]['completedAt'], iso(5))

    def test_contract_rejects_additional_private_fields(self):
        snap = self.snapshot()
        snap['agents'][0]['threadId'] = self.manager
        with self.assertRaises(AssertionError):
            assert_contract(self, snap)

    def test_future_oversized_and_unknown_schema_sources_fail_closed(self):
        for data in ({'schemaVersion': 2, 'jobs': []}, {'jobs': [], 'lastCheckAt': iso(9000)}, {'jobs': {}}):
            self.ledger_path.write_text(json.dumps(data))
            self.assertFalse(self.snapshot()['source']['available'])
        self.ledger_path.write_bytes(b' ' * (MAX_SOURCE_BYTES + 1))
        self.assertEqual(self.snapshot()['source']['issues'], ['ledger_unavailable'])

    def test_roster_removal_bounds_cached_private_state(self):
        self.update(lastObservedStatus='awaiting_approval')
        self.snapshot(at=1011)
        self.roster['members'] = self.roster['members'][:1]
        self.write()
        snap = self.snapshot(at=1012)
        self.assertEqual(len(snap['agents']), 1)
        self.assertEqual(snap['needsYou'], [])

    def test_unpinned_member_keeps_pending_request_without_stale_key(self):
        self.update(lastObservedStatus='awaiting_approval')
        self.snapshot(at=1011)
        self.slots = self.slots[:1]
        snap = self.snapshot(at=1012)
        self.assertIsNone(self.worker_agent(snap)['key'])
        self.assertEqual(len(snap['needsYou']), 1)

    def test_mutating_returned_snapshot_cannot_clear_cached_requests(self):
        self.update(lastObservedStatus='awaiting_approval')
        first = self.snapshot(at=1011)
        first['needsYou'][0]['sources'].clear()
        first['agents'][1]['label'] = 'corrupted'
        second = self.snapshot(at=1012)
        self.assertEqual(second['needsYou'][0]['sources'], ['manager_ledger'])
        self.assertEqual(self.worker_agent(second)['label'], 'Nova')


class PublicTextTests(unittest.TestCase):
    def test_controls_paths_and_credentials_are_redacted(self):
        text = public_text('Hi\nthere\u202e C:\\Users\\a\\secret.txt sk-abcdef123456 ' +
                           '123e4567-e89b-12d3-a456-426614174000', 'Fallback')
        self.assertIn('Hi there', text)
        for forbidden in ('\u202e', 'C:\\', 'secret.txt', 'sk-abcdef', '123e4567'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
