import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';
import api from '../../api/axios';
import type { TimelineResponse } from '../../types/onboarding';
import Timeline from './Timeline';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn() } }));
const workflowId = '000000000000000000abc123';
const timeline = (id = workflowId): TimelineResponse => ({ id, events: [
  { id: 0, actor: 'hr', step: 'profile', action: 'completed', detail: 'First event', created_at: '2026-11-07T17:00:00Z' },
  { id: 1, actor: 'provisioner:worker', step: 'access', action: 'provisioning.succeeded', detail: 'Second event', created_at: '2026-11-01T17:00:00Z' },
] });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

test('uses the timeline endpoint and preserves event array order, including provisioner events', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: timeline() });
  render(<Timeline workflowId={workflowId} />);
  await screen.findByText('Second event');
  expect(api.get).toHaveBeenCalledWith(`/onboarding/workflows/${workflowId}/timeline/`);
  expect(screen.getAllByText(/^(First|Second) event$/).map(node => node.textContent)).toEqual(['First event', 'Second event']);
  expect(screen.getByText(/provisioner:worker/)).toBeInTheDocument();
  fireEvent.focus(window);
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
});

test('new workflows have an empty timeline', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: { id: workflowId, events: [] } });
  render(<Timeline workflowId={workflowId} />);
  expect(await screen.findByText('No activity yet.')).toBeInTheDocument();
});

test('timeline failures are visible and a focus refresh recovers', async () => {
  vi.mocked(api.get).mockRejectedValueOnce(new Error('Offline')).mockResolvedValue({ data: timeline() });
  render(<Timeline workflowId={workflowId} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the timeline. Refresh and try again.');
  fireEvent.focus(window);
  expect(await screen.findByText('First event')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('switching workflows ignores the previous workflow response', async () => {
  let release!: (value: { data: TimelineResponse }) => void;
  vi.mocked(api.get).mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const view = render(<Timeline workflowId={workflowId} />);
  const otherId = '000000000000000000def456';
  vi.mocked(api.get).mockResolvedValue({ data: { id: otherId, events: [] } });
  view.rerender(<Timeline workflowId={otherId} />);
  await screen.findByText('No activity yet.');
  await act(async () => release({ data: timeline() }));
  expect(screen.queryByText('First event')).not.toBeInTheDocument();
  expect(screen.getByText('No activity yet.')).toBeInTheDocument();
});
