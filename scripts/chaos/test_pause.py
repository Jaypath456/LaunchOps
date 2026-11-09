"""Pause lifecycle tests with an in-memory Docker command boundary."""

import itertools
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pause


class PauseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'pauses.jsonl'
        self.path.write_text('')
        self.states = {'worker': 'running'}
        self.calls = []
        self.fail_unpause = set()
        self.docker_patch = patch.object(pause, 'docker', side_effect=self.docker)
        self.docker_patch.start()
        self.addCleanup(self.docker_patch.stop)

    def docker(self, *args, check=True):
        self.calls.append(args)
        output, error, code = '', '', 0
        if args[0] == 'ps':
            status = args[-1].removeprefix('status=')
            output = '\n'.join(c for c, state in self.states.items() if state == status)
        else:
            container = args[-1]
            if container not in self.states:
                code, error = 1, 'Error: No such container'
            elif args[0] == 'pause':
                self.states[container] = 'paused'
            elif args[0] == 'unpause':
                if container in self.fail_unpause:
                    code, error = 1, 'unpause failed'
                elif self.states[container] == 'paused':
                    self.states[container] = 'running'
                else:
                    code, error = 1, 'container is not paused'
            elif args[0] == 'inspect':
                output = str(self.states[container] == 'paused').lower()
        if check and code:
            raise subprocess.CalledProcessError(code, args, output, error)
        return SimpleNamespace(returncode=code, stdout=output, stderr=error)

    def records(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_recovers_recorded_and_unrecorded_pauses(self):
        self.states = {'worker': 'paused', 'unrecorded': 'paused'}
        records = [{'container': 'worker', 'start': 'start', 'end': None}]
        pause.recover(self.path, records)
        self.assertEqual(self.states, {'worker': 'running', 'unrecorded': 'running'})
        self.assertTrue(self.records()[0]['end'])

    def test_killed_and_removed_containers_close_pause_records(self):
        self.states = {'killed': 'exited'}
        records = [{'container': c, 'start': 'start', 'end': None} for c in ('killed', 'removed')]
        pause.recover(self.path, records)
        self.assertTrue(all(record['end'] for record in self.records()))

    def test_failure_does_not_hide_open_pause_or_skip_other_workers(self):
        self.states = {'stuck': 'paused', 'worker': 'paused'}
        self.fail_unpause.add('stuck')
        records = [{'container': c, 'start': 'start', 'end': None} for c in self.states]
        with self.assertRaisesRegex(RuntimeError, 'remains paused'):
            pause.recover(self.path, records)
        self.assertIsNone(self.records()[0]['end'])
        self.assertTrue(self.records()[1]['end'])
        self.assertEqual(self.states['worker'], 'running')

    def test_docker_connection_failure_is_not_treated_as_removal(self):
        with patch.object(pause, 'docker', return_value=SimpleNamespace(
                returncode=1, stdout='', stderr='Cannot connect to the Docker daemon')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot confirm'):
                pause.resume('worker')

    def test_removed_container_error_accepts_docker_message_variants(self):
        for error in ('Error: No such container: worker', 'error: no such object: worker'):
            with self.subTest(error=error):
                with patch.object(pause, 'docker', return_value=SimpleNamespace(
                        returncode=1, stdout='', stderr=error)):
                    pause.resume('worker')

    def test_failed_container_scan_still_recovers_known_pauses(self):
        self.states['worker'] = 'paused'
        records = [{'container': 'worker', 'start': 'start', 'end': None}]
        with patch.object(pause, 'containers', side_effect=subprocess.TimeoutExpired('docker ps', 15)):
            with self.assertRaises(RuntimeError):
                pause.recover(self.path, records)
        self.assertEqual(self.states['worker'], 'running')
        self.assertTrue(self.records()[0]['end'])

    def run_loop(self, *, duration, iterations):
        stopped = SimpleNamespace(is_set=iter([False] * iterations + [True]).__next__, wait=lambda _: None)
        with patch.object(pause.time, 'monotonic', side_effect=itertools.count()):
            pause.run(self.path, self.path.with_name('stop'), 1, duration, stopped)

    def test_scheduled_unpause_and_exit_cleanup_both_close_records(self):
        self.run_loop(duration=1, iterations=2)
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record['start'] and record['end'] for record in records))
        self.assertEqual(self.states['worker'], 'running')

    def test_active_worker_is_not_paused_again(self):
        self.run_loop(duration=100, iterations=3)
        self.assertEqual(len(self.records()), 1)
        self.assertTrue(self.records()[0]['end'])
        self.assertEqual(sum(call[0] == 'pause' for call in self.calls), 1)

    def test_exception_after_freeze_still_unpauses_worker(self):
        original = pause.save
        calls = 0

        def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('record write failed')
            original(*args)

        with patch.object(pause, 'save', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'record write failed'):
                self.run_loop(duration=100, iterations=1)
        self.assertEqual(self.states['worker'], 'running')
        self.assertTrue(self.records()[0]['end'])


if __name__ == '__main__':
    unittest.main()
