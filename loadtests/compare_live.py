"""Repeatable live quiz comparison. Fresh synthetic quiz per run, same accounts and host.

Run from repository root after `seed_demo --students N` (see README).
Never use real users or a production deployment for this benchmark.
"""
import argparse
import asyncio
import csv
import json
import platform
import time
from pathlib import Path

from ws_load_test import run


def is_valid_sample(sample, users):
    return (sample['num_students'] == users and sample['accepted_answers'] == users
            and not any(sample.get(key, 0) for key in ('failed_connections', 'missing_reveals',
                'missing_chart_updates', 'incorrect_acknowledgments', 'unconfirmed_session_ends')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http-host', default='http://localhost:18000')
    parser.add_argument('--django-ws', default='ws://localhost:18000')
    parser.add_argument('--go-ws', default='ws://localhost:19090')
    parser.add_argument('--users', type=int, nargs='+', default=[10, 25, 50, 100])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--max-attempts', type=int, default=5,
                        help='Bounded paired attempts per size to obtain --repeats valid runs per engine')
    parser.add_argument('--output', default='loadtests/reports/live_comparison.json')
    args = parser.parse_args()
    if args.repeats < 1 or args.max_attempts < args.repeats or any(n < 1 or n > 5000 for n in args.users):
        parser.error('Repeats must be >= 1 and each user count 1..5000')
    records = []
    for n in args.users:
        valid_counts = {'django': 0, 'go': 0}
        for repetition in range(args.max_attempts):
            # AB / BA order limits warmup or machine load favoring one engine.
            engines = [('django', args.django_ws), ('go', args.go_ws)]
            if repetition % 2:
                engines.reverse()
            for engine, ws in engines:
                print(f'\n--- users={n}, repeat={repetition + 1}, engine={engine} ---')
                started = time.time()
                sample = asyncio.run(run(args.http_host, ws, n, isolate=True, engine=engine))
                sample.update({'repeat': repetition + 1, 'utc_started_at': started, 'http_host': args.http_host})
                sample['valid_run'] = is_valid_sample(sample, n)
                valid_counts[engine] += int(sample['valid_run'])
                records.append(sample)
                path = Path(args.output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'environment': platform.platform(), 'runs': records}, indent=2))
            if min(valid_counts.values()) >= args.repeats:
                break
        print(f'Valid runs at N={n}: {valid_counts}; target={args.repeats}, attempt cap={args.max_attempts}')
    csv_path = Path(args.output).with_suffix('.csv')
    with csv_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['engine', 'requested_students', 'repeat', 'valid_run', 'num_students',
            'failed_connections', 'accepted_answers', 'missing_reveals', 'missing_chart_updates',
            'reveal_p50_ms', 'reveal_p95_ms', 'reveal_p99_ms', 'accept_p50_ms', 'accept_p95_ms',
            'accept_p99_ms', 'chart_p50_ms', 'chart_p95_ms', 'chart_p99_ms'])
        writer.writeheader()
        for row in records:
            writer.writerow({**{k: row[k] for k in writer.fieldnames if k in row},
                **{f'{metric}_{pct}_ms': row[f'{metric}_latency_ms'][pct] for metric in ('reveal', 'accept', 'chart')
                   for pct in ('p50', 'p95', 'p99')}})
    print(f'\nSaved raw samples to {args.output} and {csv_path}. Check errors before making any speedup claim.')
    if any(not row['valid_run'] for row in records):
        raise SystemExit('Incomplete runs recorded; do not use them to claim a speedup.')


if __name__ == '__main__':
    main()
