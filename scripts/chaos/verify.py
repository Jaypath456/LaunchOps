#!/usr/bin/env python3
"""Verify campaign evidence without changing the database or simulator."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


def verify(config, workflows, accounts, stats, logs, kills, created):
    failures = []
    reports = []
    jobs = []
    expected_names = {f'chaos-employee-{n:04d}' for n in range(1, config['hires'] + 1)}
    if len(created) != config['hires'] or {c['employee'] for c in created} != expected_names:
        failures.append('API hire manifest does not match requested employees')
    if len({c['id'] for c in created}) != len(created):
        failures.append('API hire manifest contains duplicate workflow ids')
    if len(workflows) != config['hires'] + 1:
        failures.append('Workflow count does not match hires plus seeded demo')
    if not {c['id'] for c in created}.issubset({doc['id'] for doc in workflows}):
        failures.append('Created workflow missing from snapshot')
    manifest = {c['id']: c['employee'] for c in created}
    for doc in workflows:
        if Counter(j['connector'] for j in doc['provisioning']) != Counter(['workspace', 'slack', 'github']):
            failures.append(f"{doc['id']}: missing or duplicate connector jobs")
        expected_employee = manifest.get(doc['id'], 'demo_employee')
        if any(j['payload']['username'] != expected_employee for j in doc['provisioning']):
            failures.append(f"{doc['id']}: job employee does not match API manifest or seeded demo")
        jobs.extend(doc['provisioning'])
    job_keys = [(job['connector'], job['payload']['username']) for job in jobs]
    account_keys = [(a['connector'], a['username']) for a in accounts]
    if len(set(job_keys)) != len(job_keys):
        failures.append('Duplicate job account identities')
    if len(set(account_keys)) != len(account_keys):
        failures.append('Duplicate simulator account identities')
    by_account = {(a['connector'], a['username']): a for a in accounts}
    for job in jobs:
        key = (job['connector'], job['payload']['username'])
        label = ':'.join(key)
        account = by_account.get(key)
        if job['status'] not in ('succeeded', 'dead', 'overridden'):
            failures.append(f'{label}: nonterminal status {job["status"]}')
        if job['status'] == 'succeeded' and (
            not account or not job.get('external_ref') or job['external_ref'] != account['external_ref']
        ):
            failures.append(f'{label}: succeeded external_ref does not match simulator account')
        error = job.get('last_error') or ''
        if job['status'] == 'dead' and re.search(r'\b409\b', error) and 'in progress' not in error.lower():
            failures.append(f'{label}: dead from account conflict: {error}')
        if job['status'] == 'dead' and account:
            reports.append({'connector': key[0], 'username': key[1], 'external_ref': account['external_ref'],
                            'reason': 'Account exists although job is dead; IT override required'})
    for key in set(account_keys) - set(job_keys):
        failures.append(f'{":".join(key)}: simulator account has no job')

    # Count structured finish records once per attempt: store and worker can both log rejection.
    rejected = set()
    for line in logs.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # compose startup banners are not structured attempt records
        if isinstance(record, dict) and record.get('outcome') == 'fence_rejected':
            if all(key in record for key in ('job_key', 'fence', 'worker_id')):
                rejected.add((record['job_key'], record['fence'], record['worker_id']))
    exercised = {'replays': stats['replayed'], 'fence_rejections': len(rejected),
                 'kills': len(kills), 'drops': stats['drop_after_commit_fired']}
    invariants_passed = not failures
    for fault, count in exercised.items():
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            failures.append(f'Fault not exercised: {fault}')
    return {'passed': not failures, 'invariants_passed': invariants_passed,
            'failures': failures, 'dead_with_accounts': reports,
            'counts': {'workflows': len(workflows), 'jobs': len(jobs), 'accounts': len(accounts),
                       'statuses': dict(Counter(j['status'] for j in jobs)), **exercised}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--dry-run', action='store_true',
                        help='Check tooling with kills disabled; still report full campaign verification failures.')
    args = parser.parse_args()
    root = args.evidence
    try:
        def read(name):
            return json.loads((root / name).read_text())
        config = read('config.json')
        if args.dry_run and config.get('kill_every') != 0:
            raise ValueError('Dry-run verification requires KILL_EVERY=0')
        result = verify(config, read('jobs.json'), read('mocksaas-accounts.json'),
                        read('mocksaas-stats.json'), (root / 'provisioner.log').read_text(),
                        [json.loads(line) for line in (root / 'kills.jsonl').read_text().splitlines()],
                        read('created.json'))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'passed': False, 'invariants_passed': False,
                  'failures': [f'Invalid or missing evidence: {exc}'], 'dead_with_accounts': []}
    result['mode'] = 'dry-run' if args.dry_run else 'campaign'
    if args.dry_run:
        # A successful tooling check is separate from the strict campaign result above.
        result['dry_run_passed'] = result['invariants_passed']
    (root / 'verify.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result.get('dry_run_passed', result['passed']) else 1)


if __name__ == '__main__':
    main()
