#!/usr/bin/env python3
"""Pause workers in the background and recover every pause before teardown."""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import random
import signal
import subprocess
import threading
import time


def docker(*args, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True,
                          timeout=15, check=check)


def containers(status):
    return docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=launchops-chaos',
                  '--filter', 'label=com.docker.compose.service=provisioner',
                  '--filter', f'status={status}').stdout.split()


def save(path, records):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(''.join(json.dumps(record) + '\n' for record in records))
    temporary.replace(path)


def resume(container):
    # A kill/removal racing with unpause is harmless. A worker still paused is not.
    docker('unpause', container, check=False)
    state = docker('inspect', '--format', '{{.State.Paused}}', container, check=False)
    if state.returncode == 0 and state.stdout.strip() == 'true':
        raise RuntimeError(f'Container remains paused: {container}')
    if state.returncode != 0 and 'no such' not in state.stderr.lower():
        raise RuntimeError(f'Cannot confirm container is unpaused: {state.stderr}')


def recover(path, records):
    # Include unrecorded pauses: termination can arrive between docker pause and save.
    targets = {record['container'] for record in records if not record.get('end')}
    errors = []
    try:
        targets.update(containers('paused'))
    except subprocess.SubprocessError as exc:
        errors.append(str(exc))
    for container in targets:
        try:
            resume(container)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue
        for record in records:
            if record['container'] == container and not record.get('end'):
                record['end'] = datetime.now(UTC).isoformat()
    save(path, records)
    if errors:
        raise RuntimeError('; '.join(errors))


def run(path, stop_file, every, duration, stopped):
    records = [json.loads(line) for line in path.read_text().splitlines()]
    active = {}
    next_pause = time.monotonic() + every
    try:
        while not stopped.is_set() and not stop_file.exists():
            now = time.monotonic()
            for container, (deadline, record) in list(active.items()):
                if now >= deadline:
                    resume(container)
                    record['end'] = datetime.now(UTC).isoformat()
                    save(path, records)
                    del active[container]
            if now >= next_pause:
                # Exclude active pauses and confirm state to avoid extending a freeze
                # or racing with a container killed/removed by the other loop.
                available = [c for c in containers('running') if c not in active]
                if available:
                    container = random.choice(available)
                    state = docker('inspect', '--format', '{{.State.Paused}}', container, check=False)
                    if state.returncode == 0 and state.stdout.strip() == 'false':
                        if docker('pause', container, check=False).returncode == 0:
                            record = {'container': container, 'start': datetime.now(UTC).isoformat(), 'end': None}
                            records.append(record)
                            save(path, records)
                            active[container] = (time.monotonic() + duration, record)
                next_pause = time.monotonic() + every
            stopped.wait(0.2)
    finally:
        recover(path, records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records', type=Path)
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--every', type=float)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    if args.cleanup:
        recover(args.records, [json.loads(line) for line in args.records.read_text().splitlines()])
        return
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    run(args.records, args.stop_file, args.every, args.duration, stopped)


if __name__ == '__main__':
    main()
