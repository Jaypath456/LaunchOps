"""Describe all valid samples; keep failures visible, and never compare mismatched N."""
import argparse
import json
import statistics
from pathlib import Path
from ws_load_test import percentile


def summarize(path):
    records = json.loads(Path(path).read_text())['runs']
    groups = []
    for users in sorted({row['requested_students'] for row in records}):
        for engine in ('django', 'go'):
            rows = [row for row in records if row['requested_students'] == users and row['engine'] == engine]
            valid = [row for row in rows if row['valid_run']]
            metrics = {}
            for metric in ('reveal', 'accept'):
                samples = [value for row in valid for value in row['latency_samples_ms'][metric]]
                metrics[metric] = {'sample_count': len(samples), **{f'p{pct}_ms': percentile(samples, pct) for pct in (50, 95, 99)}}
            charts = [value for row in valid for value in row['latency_samples_ms']['chart']]
            groups.append({'users': users, 'engine': engine, 'valid_runs': len(valid), 'attempts': len(rows),
                'invalid_runs': len(rows)-len(valid), 'requested_connections': sum(row['requested_students'] for row in rows),
                'connected': sum(row['num_students'] for row in rows), 'accepted': sum(row['accepted_answers'] for row in rows),
                'connection_failures': sum(row['failed_connections'] for row in rows),
                'missing_reveals': sum(row['missing_reveals'] for row in rows),
                'missing_charts': sum(row['missing_chart_updates'] for row in rows),
                'metrics': metrics, 'chart': {'sample_count': len(charts),
                    'median_ms': statistics.median(charts) if charts else None,
                    'min_ms': min(charts) if charts else None, 'max_ms': max(charts) if charts else None}})
    result = {'source': str(path), 'methodology': 'All valid runs included. Reveal/ack percentiles pool learner observations; observations within a run are correlated. Chart has one sample/run: report median/range, no reliable p95/p99 at 3–5 runs. Invalid runs excluded from latency comparison but retained in counts and raw source.', 'groups': groups}
    Path(path).with_name('benchmark-summary.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results')
    args = parser.parse_args()
    print(json.dumps(summarize(args.results), indent=2))
