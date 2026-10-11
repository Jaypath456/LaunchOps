"""Renders the before/after comparison graphs from raw load-test output.

Inputs (all optional - each graph is skipped with a warning if its
inputs aren't found, rather than failing the whole run):

  REST tier (M20): Locust --csv output, one pair of files per user
  count, named exactly as loadtests/README.md's ladder methodology
  produces them:
      loadtests/reports/before_<N>users_stats.csv
      loadtests/reports/after_<N>users_stats.csv
  for each <N> in --user-counts.

  WebSocket tier (M21): a JSON-lines file produced by running
  ws_load_test.py --output-json at several --students values, e.g.:
      for n in 50 100 200 500; do
        python loadtests/ws_load_test.py --host ... --students $n \\
          --output-json loadtests/reports/ws_ladder.jsonl
      done

Usage:
    python loadtests/generate_graphs.py \\
      --user-counts 100 500 1000 \\
      --ws-jsonl loadtests/reports/ws_ladder.jsonl \\
      --out-dir loadtests/reports
"""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')  # headless - no display server needed to render PNGs
import matplotlib.pyplot as plt

REST_ENDPOINTS_OF_INTEREST = [
    'GET /api/courses/',
    'POST /api/quizzes/[id]/submit/',
]


def read_locust_stats(csv_path):
    """Returns {request_name: {'p50': ms, 'p95': ms}} from a Locust
    *_stats.csv file, or None if the file doesn't exist."""
    path = Path(csv_path)
    if not path.exists():
        return None

    stats = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            name = row.get('Name', '').strip()
            if name in REST_ENDPOINTS_OF_INTEREST:
                stats[name] = {
                    'p50': float(row.get('50%', 0) or 0),
                    'p95': float(row.get('95%', 0) or 0),
                }
    return stats


def plot_rest_comparison(reports_dir, user_counts, out_dir):
    reports_dir = Path(reports_dir)
    before_by_count = {}
    after_by_count = {}

    for count in user_counts:
        before = read_locust_stats(reports_dir / f'before_{count}users_stats.csv')
        after = read_locust_stats(reports_dir / f'after_{count}users_stats.csv')
        if before is not None:
            before_by_count[count] = before
        if after is not None:
            after_by_count[count] = after

    if not before_by_count or not after_by_count:
        print(
            'Skipping REST comparison graph: missing before_/after_ CSVs in '
            f'{reports_dir} for user counts {user_counts}. See loadtests/README.md '
            'for how to produce them.'
        )
        return

    for endpoint in REST_ENDPOINTS_OF_INTEREST:
        counts_with_data = [
            c for c in user_counts
            if endpoint in before_by_count.get(c, {}) and endpoint in after_by_count.get(c, {})
        ]
        if not counts_with_data:
            continue

        before_p95 = [before_by_count[c][endpoint]['p95'] for c in counts_with_data]
        after_p95 = [after_by_count[c][endpoint]['p95'] for c in counts_with_data]

        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(counts_with_data, before_p95, marker='o', label='Before (Redis optimizations off)', color='#dc2626')
        ax.plot(counts_with_data, after_p95, marker='o', label='After (Redis optimizations on)', color='#4f46e5')
        ax.set_xlabel('Concurrent users')
        ax.set_ylabel('p95 latency (ms)')
        ax.set_title(f'{endpoint} - p95 latency vs. concurrent users')
        ax.legend()
        ax.grid(True, alpha=0.3)

        safe_name = endpoint.replace('/', '_').replace(' ', '_').replace('[', '').replace(']', '')
        out_path = Path(out_dir) / f'rest_before_after{safe_name}.png'
        fig.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f'Wrote {out_path}')


def plot_ws_ladder(jsonl_path, out_dir):
    path = Path(jsonl_path) if jsonl_path else None
    if path is None or not path.exists():
        print(f'Skipping WebSocket ladder graph: {jsonl_path} not found. Run ws_load_test.py --output-json first.')
        return

    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        print(f'Skipping WebSocket ladder graph: {jsonl_path} is empty.')
        return

    records.sort(key=lambda r: r['num_students'])
    student_counts = [r['num_students'] for r in records]
    reveal_p95 = [r['reveal_latency_ms']['p95'] for r in records]
    chart_p95 = [r['chart_latency_ms']['p95'] for r in records]

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(student_counts, reveal_p95, marker='o', label='question.revealed delivery (p95)', color='#059669')
    ax.plot(student_counts, chart_p95, marker='o', label='chart.update round-trip (p95)', color='#4f46e5')
    ax.set_xlabel('Concurrently connected students')
    ax.set_ylabel('p95 latency (ms)')
    ax.set_title('Live quiz session: latency vs. room size')
    ax.legend()
    ax.grid(True, alpha=0.3)

    out_path = Path(out_dir) / 'ws_latency_vs_room_size.png'
    fig.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'Wrote {out_path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--reports-dir', default='loadtests/reports', help='Directory containing Locust --csv output (default: loadtests/reports)'
    )
    parser.add_argument(
        '--user-counts', type=int, nargs='+', default=[100, 500, 1000],
        help='User counts to look for before_/after_ CSVs at (default: 100 500 1000)',
    )
    parser.add_argument(
        '--ws-jsonl', default='loadtests/reports/ws_ladder.jsonl',
        help='JSON-lines file produced by ws_load_test.py --output-json (default: loadtests/reports/ws_ladder.jsonl)',
    )
    parser.add_argument('--out-dir', default='loadtests/reports', help='Where to write the generated PNGs')
    args = parser.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    plot_rest_comparison(args.reports_dir, args.user_counts, args.out_dir)
    plot_ws_ladder(args.ws_jsonl, args.out_dir)


if __name__ == '__main__':
    main()
