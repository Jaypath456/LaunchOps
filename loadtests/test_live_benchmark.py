"""A failed setup must remain a recorded sample, not abort the whole ladder."""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import ws_load_test as bench
from compare_live import is_valid_sample


class FailedConnectionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_question_acknowledgment_cannot_validate_run(self):
        socket = AsyncMock()
        socket.recv.return_value = json.dumps({'type': 'answer.accepted', 'question_id': 3, 'is_correct': True})
        ack = await bench.submit_and_wait_for_chart(socket, 2, bench.time.monotonic())
        self.assertFalse(ack['correct'])
        self.assertFalse(is_valid_sample({'num_students': 1, 'accepted_answers': 1,
                                          'incorrect_acknowledgments': 1}, 1))

    async def test_total_connection_failure_returns_serializable_sample(self):
        host = AsyncMock()
        host.recv.side_effect = [json.dumps({'type': 'session.state'}), json.dumps({'type': 'session.ended'})]
        response = MagicMock()
        response.json.return_value = {'room_code': 'ABC123'}
        with patch.object(bench, 'http_login', return_value='token'), \
             patch.object(bench, 'resolve_quiz', return_value=(1, 2)), \
             patch.object(bench.requests, 'post', return_value=response), \
             patch.object(bench.websockets, 'connect', AsyncMock(return_value=host)), \
             patch.object(bench, 'authenticate_student', AsyncMock(return_value=('token', None))), \
             patch.object(bench, 'connect_student', AsyncMock(return_value=(None, 'handshake failed'))):
            result = await bench.run('http://unused', 'ws://unused', 2)
        self.assertEqual(result['requested_students'], 2)
        self.assertEqual(result['num_students'], 0)
        self.assertEqual(result['failed_connections'], 2)
        self.assertEqual(result['accepted_answers'], 0)
        self.assertEqual(result['connection_errors'], ['handshake failed'] * 2)
        self.assertIsNone(result['accept_latency_ms']['p95'])
        json.dumps(result, allow_nan=False)
        host.close.assert_awaited_once()
