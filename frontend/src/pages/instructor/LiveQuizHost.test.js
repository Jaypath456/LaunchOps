import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import LiveQuizHost from './LiveQuizHost';
import api from '../../api/axios';
import { ReconnectingSocket } from '../../api/ws';

jest.mock('react-router-dom', () => ({ useParams: () => ({ quizId: '1' }) }), { virtual: true });
jest.mock('../../api/axios', () => ({ post: jest.fn(), get: jest.fn() }));
jest.mock('../../api/ws', () => ({ ReconnectingSocket: jest.fn() }));

it('restores ended state and saved scores when reconnect misses session.ended', async () => {
  const session = { room_code: 'ABC123', engine: 'go', status: 'lobby' };
  api.post.mockResolvedValueOnce({ data: session })
    .mockResolvedValueOnce({ data: { ...session, status: 'active' } });
  api.get.mockResolvedValue({ data: { scores: [{ user_id: 2, username: 'learner', score: 2, answered: 1 }] } });
  ReconnectingSocket.mockImplementation(() => ({ close: jest.fn() }));
  render(<LiveQuizHost />);
  fireEvent.click(screen.getByRole('button', { name: 'Create Live Session' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Start Session' }));
  await screen.findByRole('button', { name: 'Reveal First Question' });
  expect(screen.getByRole('button', { name: 'Reveal First Question' })).toBeDisabled();
  act(() => ReconnectingSocket.mock.calls[0][1].onOpen());
  expect(screen.getByRole('button', { name: 'Reveal First Question' })).toBeEnabled();
  const handler = ReconnectingSocket.mock.calls[0][1].onMessage;
  await act(async () => handler({ data: JSON.stringify({ type: 'session.state', status: 'ended', question_index: 0 }) }));
  expect(await screen.findByText('Session ABC123 has ended.')).toBeInTheDocument();
  expect(await screen.findByText('learner: 2 points (1 answers)')).toBeInTheDocument();
});
