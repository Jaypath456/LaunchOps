"""Offline invariant regression tests; fixtures are synthetic, never campaign evidence."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from verify import verify


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.config = {'hires': 1, 'kill_every': 10, 'pause_every': 10}
        self.created = [{'id': 'hire', 'employee': 'chaos-employee-0001'}]
        self.workflows = []
        self.accounts = []
        for workflow_id, username in [('demo', 'demo_employee'), ('hire', 'chaos-employee-0001')]:
            jobs = []
            for connector in ['workspace', 'slack', 'github']:
                ref = f'{username}:{connector}'
                jobs.append({'connector': connector, 'payload': {'username': username},
                             'status': 'succeeded', 'external_ref': ref, 'last_error': None})
                self.accounts.append({'connector': connector, 'username': username, 'external_ref': ref})
            self.workflows.append({'id': workflow_id, 'provisioning': jobs})
        self.stats = {'replayed': 1, 'drop_after_commit_fired': 1}
        self.logs = json.dumps({'outcome': 'fence_rejected', 'job_key': 'hire:workspace',
                                'fence': 1, 'worker_id': 'worker'})
        self.kills = [{'container': 'worker', 'at': 'synthetic'}]
        self.pauses = [{'container': 'worker', 'start': 'synthetic start', 'end': 'synthetic end'}]

    def result(self):
        return verify(self.config, self.workflows, self.accounts, self.stats, self.logs, self.kills,
                      self.created, self.pauses)

    def test_complete_campaign_passes(self):
        self.assertTrue(self.result()['passed'])

    def test_all_nonterminal_and_unknown_states_fail(self):
        for status in ['pending', 'running', 'retry', 'unknown']:
            with self.subTest(status=status):
                self.workflows[0]['provisioning'][0]['status'] = status
                self.assertFalse(self.result()['passed'])

    def test_succeeded_requires_matching_account_reference(self):
        for reference in [None, '', 'wrong']:
            with self.subTest(reference=reference):
                self.workflows[0]['provisioning'][0]['external_ref'] = reference
                self.assertFalse(self.result()['passed'])
        self.accounts.pop()
        self.assertFalse(self.result()['passed'])

    def test_dead_conflict_fails_but_in_progress_is_not_account_conflict(self):
        job = self.workflows[0]['provisioning'][0]
        job.update(status='dead', last_error='mock-saas workspace: HTTP 409: {"error":"account already exists"}')
        self.assertFalse(self.result()['passed'])
        job['last_error'] = 'mock-saas workspace: HTTP 409: {"error":"in progress"}'
        self.assertTrue(self.result()['passed'])

    def test_dead_account_is_reported_even_without_invariant_failure(self):
        self.workflows[0]['provisioning'][0].update(status='dead', last_error='timeout')
        result = self.result()
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['dead_with_accounts']), 1)
        self.accounts.pop(0)
        self.assertEqual(self.result()['dead_with_accounts'], [])

    def test_orphan_and_duplicate_accounts_fail(self):
        self.accounts.append({'connector': 'slack', 'username': 'orphan', 'external_ref': 'other'})
        self.assertFalse(self.result()['passed'])
        self.accounts[-1] = deepcopy(self.accounts[0])
        self.assertFalse(self.result()['passed'])

    def test_missing_and_duplicate_jobs_fail(self):
        job = self.workflows[0]['provisioning'].pop()
        self.assertFalse(self.result()['passed'])
        self.workflows[0]['provisioning'].extend([job, deepcopy(job)])
        self.assertFalse(self.result()['passed'])

    def test_hire_manifest_and_snapshot_must_agree(self):
        self.created[0]['employee'] = 'wrong'
        self.assertFalse(self.result()['passed'])
        self.created[0]['employee'] = 'chaos-employee-0001'
        self.created[0]['id'] = 'missing'
        self.assertFalse(self.result()['passed'])
        self.created.clear()
        self.assertFalse(self.result()['passed'])

    def test_each_required_fault_is_mandatory(self):
        for key in self.stats:
            with self.subTest(fault=key):
                self.stats[key] = 0
                self.assertFalse(self.result()['passed'])
                self.stats[key] = 1
        self.logs = 'banner\n' + json.dumps({'outcome': 'not_fence_rejected'})
        self.assertFalse(self.result()['passed'])
        self.logs = json.dumps({'outcome': 'fence_rejected', 'job_key': 'hire:workspace',
                                'fence': 1, 'worker_id': 'worker'})
        self.kills.clear()
        self.config['kill_every'] = 0
        self.assertFalse(self.result()['passed'])

    def test_duplicate_fence_logs_count_one_attempt(self):
        self.logs = self.logs + '\n' + self.logs
        self.assertEqual(self.result()['counts']['fence_rejections'], 1)

    def test_fence_rejection_is_not_required_without_pauses(self):
        self.pauses.clear()
        self.logs = ''
        for interval in (0, 10):
            with self.subTest(pause_every=interval):
                self.config['pause_every'] = interval
                result = self.result()
                self.assertTrue(result['passed'])
                self.assertEqual(result['counts']['pauses'], 0)
                self.assertEqual(result['fault_requirements']['fence_rejections'], 'not required')
                self.assertNotIn('Fault not exercised: fence_rejections', result['failures'])

    def test_pause_requires_fence_rejection_even_if_interval_is_disabled(self):
        self.logs = ''
        self.config['pause_every'] = 0
        result = self.result()
        self.assertFalse(result['passed'])
        self.assertTrue(result['invariants_passed'])
        self.assertEqual(result['counts']['pauses'], 1)
        self.assertEqual(result['fault_requirements']['fence_rejections'], 'required')
        self.assertIn('Fault not exercised: fence_rejections', result['failures'])

    def test_pause_without_end_time_fails_invariants(self):
        for end in (None, ''):
            with self.subTest(end=end):
                self.pauses[0]['end'] = end
                self.assertFalse(self.result()['invariants_passed'])
                self.assertIn('Pause has no end time: worker', self.result()['failures'])
        del self.pauses[0]['end']
        self.assertFalse(self.result()['passed'])

    def test_latency_count_is_reported_without_requiring_latency(self):
        self.assertEqual(self.result()['counts']['latency_fired'], 0)
        self.stats['latency_fired'] = 3
        self.assertEqual(self.result()['counts']['latency_fired'], 3)
        self.stats['latency_fired'] = 0
        self.assertTrue(self.result()['passed'])

    def test_overridden_is_terminal(self):
        self.workflows[0]['provisioning'][0]['status'] = 'overridden'
        self.assertTrue(self.result()['passed'])

    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('config.json', self.config), ('jobs.json', self.workflows),
                                ('mocksaas-accounts.json', self.accounts),
                                ('mocksaas-stats.json', self.stats), ('created.json', self.created)]:
                (root / name).write_text(json.dumps(value))
            (root / 'provisioner.log').write_text(self.logs)
            (root / 'kills.jsonl').write_text(''.join(json.dumps(kill) + '\n' for kill in self.kills))
            (root / 'pauses.jsonl').write_text(''.join(json.dumps(pause) + '\n' for pause in self.pauses))
            command = [sys.executable, str(Path(__file__).with_name('verify.py')), directory, *args]
            process = subprocess.run(command, capture_output=True, text=True, check=False)
            return process.returncode, json.loads((root / 'verify.json').read_text())

    def disable_faults(self):
        self.config['kill_every'] = 0
        self.config['pause_every'] = 0
        self.stats = {key: 0 for key in self.stats}
        self.logs = ''
        self.kills.clear()
        self.pauses.clear()

    def test_explicit_dry_run_checks_invariants_without_claiming_campaign_pass(self):
        self.disable_faults()
        code, result = self.run_cli('--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(result['mode'], 'dry-run')
        self.assertTrue(result['dry_run_passed'])
        self.assertTrue(result['invariants_passed'])
        self.assertFalse(result['passed'])
        self.assertEqual(set(result['failures']), {
            f'Fault not exercised: {fault}' for fault in ('replays', 'kills', 'drops')})

    def test_disabled_kills_do_not_relax_default_campaign_cli(self):
        self.disable_faults()
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['mode'], 'campaign')
        self.assertFalse(result['passed'])
        self.assertNotIn('dry_run_passed', result)

    def test_dry_run_rejects_job_account_and_manifest_failures(self):
        self.disable_faults()
        for failure in ('nonterminal', 'reference', 'conflict', 'orphan', 'manifest'):
            with self.subTest(failure=failure):
                workflows, accounts, created = deepcopy((self.workflows, self.accounts, self.created))
                job = self.workflows[0]['provisioning'][0]
                if failure == 'nonterminal':
                    job['status'] = 'running'
                elif failure == 'reference':
                    job['external_ref'] = 'wrong'
                elif failure == 'conflict':
                    job.update(status='dead', last_error='HTTP 409 account already exists')
                elif failure == 'orphan':
                    self.accounts.append({'connector': 'slack', 'username': 'orphan', 'external_ref': 'other'})
                else:
                    self.created.clear()
                code, result = self.run_cli('--dry-run')
                self.assertEqual(code, 1)
                self.assertFalse(result['dry_run_passed'])
                self.assertFalse(result['invariants_passed'])
                self.assertFalse(result['passed'])
                self.workflows, self.accounts, self.created = workflows, accounts, created

    def test_dry_run_refuses_configuration_with_kills_enabled(self):
        code, result = self.run_cli('--dry-run')
        self.assertEqual(code, 1)
        self.assertFalse(result['dry_run_passed'])
        self.assertIn('Dry-run verification requires KILL_EVERY=0', result['failures'][0])

    def test_dry_run_refuses_configuration_with_pauses_enabled(self):
        self.disable_faults()
        self.config['pause_every'] = 1
        code, result = self.run_cli('--dry-run')
        self.assertEqual(code, 1)
        self.assertIn('Dry-run verification requires PAUSE_EVERY=0', result['failures'][0])

    def test_dry_run_refuses_pause_records(self):
        self.disable_faults()
        self.pauses.append({'container': 'worker', 'start': 'start', 'end': None})
        code, result = self.run_cli('--dry-run')
        self.assertEqual(code, 1)
        self.assertFalse(result['dry_run_passed'])

    def test_dry_run_missing_evidence_still_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('verify.py')),
                                     directory, '--dry-run'], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads((Path(directory) / 'verify.json').read_text())['dry_run_passed'])

    def test_cli_missing_evidence_writes_failure_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('verify.py')), directory],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads((Path(directory) / 'verify.json').read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
