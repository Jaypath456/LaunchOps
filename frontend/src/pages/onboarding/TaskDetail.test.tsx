import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import api from '../../api/axios';
import type { TaskDetailResponse } from '../../types/onboarding';
import TaskDetail from './TaskDetail';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'employee', role: 'employee' } }) }));

const task = (): TaskDetailResponse => ({
  workflow: {
    id: 1, employee: 'employee', manager: 'manager', job_title: 'Engineer',
    department: 'Engineering', start_date: '2026-11-05', date_needs_correction: false,
  },
  step: {
    id: 2, key: 'training', title: 'Acknowledge security policy',
    description: 'Read and acknowledge the security policy.', owner_role: 'employee',
    kind: 'training', due_at: '2026-11-08T17:00:00Z', completed_at: null,
    prerequisites: ['access'], state: 'ready', completion_mode: 'attestation', evidence: {},
    form_fields: [{ name: 'confirmed', label: 'I read and acknowledge the security policy', type: 'checkbox' }],
  },
  dependencies: [{ id: 1, title: 'Provision work access', completed: true }],
  review: [], handbook: null, issues: [],
});

function renderTask() {
  render(<MemoryRouter initialEntries={['/workspace/tasks/2']}>
    <Routes><Route path="/workspace/tasks/:stepId" element={<TaskDetail />} /></Routes>
  </MemoryRouter>);
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });

test('training submits the acknowledgment form and displays the saved policy proof', async () => {
  const response = task();
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockImplementation(async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...response,
      step: {
        ...response.step, state: 'completed', completed_at: '2026-11-05T17:00:00Z', completed_by: 'employee',
        evidence: { confirmed: true, _proof: {
          mode: 'attestation', version: 'LaunchOps demo security policy v1',
          result: 'Acknowledgment recorded; not proof of reading or identity.',
        } },
      },
    } });
    return { data: { id: 2, changed: true, state: 'completed' } };
  });
  renderTask();
  const confirmation = await screen.findByRole('checkbox', { name: 'I read and acknowledge the security policy' });
  expect(confirmation).toBeRequired();
  expect(confirmation).not.toBeChecked();
  fireEvent.click(confirmation);
  fireEvent.click(screen.getByRole('button', { name: 'Save record and complete task →' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/onboarding/steps/2/complete/', { evidence: { confirmed: true } }));
  expect(await screen.findByText('LaunchOps demo security policy v1')).toBeInTheDocument();
  expect(screen.getByText('attestation: Acknowledgment recorded; not proof of reading or identity.')).toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

test('training acknowledgment waits for work access', async () => {
  const response = task();
  response.step = { ...response.step, state: 'blocked', completed_at: null };
  response.dependencies[0].completed = false;
  vi.mocked(api.get).mockResolvedValue({ data: response });
  renderTask();
  expect(await screen.findByRole('checkbox')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Waiting for prerequisites' })).toBeDisabled();
  expect(api.post).not.toHaveBeenCalled();
});
