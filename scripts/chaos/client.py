#!/usr/bin/env python3
"""Local campaign API client and read-only evidence collector (run in backend)."""

import argparse
from datetime import UTC, datetime
import json
import os
import time
from urllib.request import Request, urlopen


def request(base, path, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def create(hires):
    base = 'http://127.0.0.1:8000/api'
    token = request(base, '/auth/login/', {
        'username': 'demo_hr', 'password': 'LaunchOpsDemo2026!', 'role': 'hr',
    })['access']
    people = {u['username']: u for u in request(base, '/onboarding/directory/', token=token)['users']}
    created = []
    for number in range(1, hires + 1):
        name = f'chaos-employee-{number:04d}'
        workflow = request(base, '/onboarding/workflows/', {
            'employee_id': people[name]['id'], 'manager_id': people['demo_manager']['id'],
            'job_title': 'Chaos campaign', 'department': 'Local simulator',
            'start_date': datetime.now(UTC).date().isoformat(),
        }, token)
        created.append({'id': workflow['id'], 'employee': name})
    return created


def snapshot():
    # Read only; hires and their provisioning jobs are created exclusively through the API.
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms_project.settings')
    import django
    django.setup()
    from onboarding.db import get_db

    docs = get_db()['workflow_instances'].find({}, {'provisioning': 1}, max_time_ms=5000)
    return [{'id': str(doc['_id']), 'provisioning': doc.get('provisioning', [])} for doc in docs]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'wait', 'snapshot', 'faults', 'accounts', 'stats'])
    parser.add_argument('--hires', type=int, default=100)
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--error-rate', type=float, default=0.2)
    parser.add_argument('--hang-rate', type=float, default=0.05)
    parser.add_argument('--drop-rate', type=float, default=0.1)
    parser.add_argument('--latency-ms', type=int, default=0)
    args = parser.parse_args()
    if args.action == 'create':
        result = create(args.hires)
    elif args.action in ('snapshot', 'wait'):
        deadline = time.monotonic() + args.timeout
        while True:
            result = snapshot()
            jobs = [job for doc in result for job in doc['provisioning']]
            if args.action == 'snapshot' or (
                len(result) == args.hires + 1 and len(jobs) == (args.hires + 1) * 3
                and all(j['status'] in ('succeeded', 'dead', 'overridden') for j in jobs)
            ) or time.monotonic() >= deadline:
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
    else:
        body = None
        if args.action == 'faults':
            body = {'error_rate': args.error_rate, 'hang_rate': args.hang_rate,
                    'drop_after_commit_rate': args.drop_rate, 'latency_ms': args.latency_ms}
        result = request('http://mock-saas:8080', '/admin/' + args.action, body)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
