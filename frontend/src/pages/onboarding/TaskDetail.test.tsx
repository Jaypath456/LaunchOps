import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { vi } from 'vitest';
import api from '../../api/axios';
import type { User } from '../../types/auth';
import type { CompletionResponse, TaskDetailResponse } from '../../types/onboarding';
import TaskDetail from './TaskDetail';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user }) }));
const user: User = { username: 'employee', role: 'employee' };
const workflowId = '000000000000000000abc123';
const issueId = '000000000000000000def456';
const stepUrl = `/onboarding/workflows/${workflowId}/steps/training/`;
const task = (): TaskDetailResponse => ({
  workflow: {
    id: workflowId, employee: 'employee', manager: 'manager', job_title: 'Engineer',
    department: 'Engineering', start_date: '2026-11-05', date_needs_correction: false,
  },
  step: {
    key: 'training', title: 'Acknowledge security policy',
    description: 'Read and acknowledge the security policy.', owner_role: 'employee',
    kind: 'training', due_at: '2026-11-08T17:00:00Z', completed_at: null, overdue: false,
    prerequisites: ['access'], state: 'ready', completion_mode: 'attestation', evidence: {},
    form_fields: [{ name: 'confirmed', label: 'I read and acknowledge the security policy', type: 'checkbox' }],
  },
  dependencies: [{ key: 'access', title: 'Provision work access', completed: true }],
  review: [], handbook: null, issues: [], provisioning: null,
});
const completion: CompletionResponse = {
  key: 'training', state: 'done', completion: {
    by: '000000000000000000123abc', at: '2026-11-05T17:00:00Z',
    evidence: { confirmed: true }, proof: null, request_id: 'request-key',
  },
};
function renderTask(key = 'training') {
  return render(<MemoryRouter initialEntries={[`/workspace/workflows/${workflowId}/tasks/${key}`]}>
    <Routes><Route path="/workspace/workflows/:id/tasks/:key" element={<TaskDetail />} /></Routes>
  </MemoryRouter>);
}
async function fillAndSubmit() {
  const checkbox = await screen.findByRole('checkbox', { name: 'I read and acknowledge the security policy' });
  await act(async () => {
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: 'Save record and complete task →' }));
  });
}

beforeEach(() => { user.username = 'employee'; user.role = 'employee'; });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

test('submits by workflow and key and displays saved proof for a replay response', async () => {
  const response = task();
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockImplementation(async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...response,
      step: {
        ...response.step, state: 'done', completed_at: completion.completion.at, completed_by: 'employee',
        evidence: { confirmed: true, _proof: {
          mode: 'attestation', version: 'LaunchOps demo security policy v1',
          result: 'Acknowledgment recorded; not proof of reading or identity.',
        } },
      },
    } });
    return { data: completion, headers: { 'idempotent-replayed': 'true' } };
  });
  renderTask();
  const confirmation = await screen.findByRole('checkbox');
  expect(confirmation).toBeRequired();
  expect(confirmation).not.toBeChecked();
  expect(screen.getByRole('link', { name: 'Provision work access' })).toHaveAttribute('href', `/workspace/workflows/${workflowId}/tasks/access`);
  await fillAndSubmit();
  await waitFor(() => expect(api.post).toHaveBeenCalledWith(`${stepUrl}complete/`, { evidence: { confirmed: true } }, {
    headers: { 'Idempotency-Key': expect.stringMatching(/^[\da-f-]{36}$/) },
  }));
  expect(await screen.findByText('LaunchOps demo security policy v1')).toBeInTheDocument();
  expect(screen.getByText('attestation: Acknowledgment recorded; not proof of reading or identity.')).toBeInTheDocument();
  expect(screen.getByText('Completed task')).toBeInTheDocument();
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

test('double submits send one request; uncertain retries reuse the key and evidence', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: task() });
  let reject!: (reason: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise((_resolve, rejectRequest) => { reject = rejectRequest; }));
  renderTask();
  await fillAndSubmit();
  const form = screen.getByRole('checkbox').closest('form')!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(api.post).toHaveBeenCalledTimes(1);
  const first = vi.mocked(api.post).mock.calls[0];
  await act(async () => reject(new Error('Connection lost after commit')));
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not save. Try again.');
  vi.mocked(api.post).mockResolvedValue({ data: completion });
  fireEvent.submit(form);
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.post).mock.calls[1]).toEqual(first);
});

test('editing evidence starts a new submit attempt with a new key', async () => {
  const response = task();
  response.step.form_fields = [{ name: 'note', label: 'Review note', type: 'text' }];
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockRejectedValue(new Error('Offline'));
  renderTask();
  const input = await screen.findByLabelText('Review note');
  fireEvent.change(input, { target: { value: 'First review' } });
  fireEvent.submit(input.closest('form')!);
  await screen.findByRole('alert');
  const firstKey = vi.mocked(api.post).mock.calls[0][2]?.headers?.['Idempotency-Key'];
  fireEvent.change(input, { target: { value: 'Corrected review' } });
  fireEvent.submit(input.closest('form')!);
  await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.post).mock.calls[1][2]?.headers?.['Idempotency-Key']).not.toBe(firstKey);
  expect(vi.mocked(api.post).mock.calls[1][1]).toEqual({ evidence: { note: 'Corrected review' } });
});

test('completion conflicts give clear refresh guidance and do not report success', async () => {
  vi.mocked(api.get).mockResolvedValue({ data: task() });
  vi.mocked(api.post).mockRejectedValue({ response: { status: 409, data: { detail: 'Retry exhausted.' } } });
  renderTask();
  await fillAndSubmit();
  expect(await screen.findByRole('alert')).toHaveTextContent('Someone else changed this workflow; refresh and try again.');
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

test('overdue badge follows the server flag', async () => {
  const response = task();
  response.step.overdue = true;
  response.step.due_at = '2999-01-01T17:00:00Z';
  vi.mocked(api.get).mockResolvedValue({ data: response });
  renderTask();
  expect(await screen.findByText('Overdue')).toBeInTheDocument();
});

test('HR resolves an issue using string workflow and issue ids and accepts {changed}', async () => {
  user.role = 'hr';
  const response = task();
  response.step.owner_role = 'hr';
  response.issues = [{ id: issueId, detail: 'Needs support', resolution: '', resolved_at: null }];
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockImplementation(async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...response, issues: [{ ...response.issues[0], resolution: 'Support arranged', resolved_at: '2026-11-05T17:00:00Z' }],
    } });
    return { data: { changed: false } };
  });
  renderTask();
  fireEvent.change(await screen.findByLabelText('Resolution (no sensitive details)'), { target: { value: 'Support arranged' } });
  fireEvent.click(screen.getByRole('button', { name: 'Record resolution' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith(`/onboarding/workflows/${workflowId}/issues/${issueId}/resolve/`, { resolution: 'Support arranged' }));
  expect(await screen.findByText('Resolved (HR self-report)')).toBeInTheDocument();
});

test('issue resolution conflicts give the refresh message', async () => {
  user.role = 'hr';
  const response = task();
  response.issues = [{ id: issueId, detail: 'Needs support', resolution: '', resolved_at: null }];
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockRejectedValue({ response: { status: 409 } });
  renderTask();
  fireEvent.click(await screen.findByRole('button', { name: 'Record resolution' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Someone else changed this workflow; refresh and try again.');
});

function accessTask(): TaskDetailResponse {
  const response = task();
  response.step = {
    ...response.step, key: 'access', title: 'Provision work access', owner_role: 'it',
    form_fields: [{ name: 'ticket', label: 'Access request ticket', type: 'text' }],
  };
  response.provisioning = [{
    connector: 'workspace', status: 'dead', attempts: 5, last_error: 'Simulated timeout',
    lease_owner: 'simulated-worker', external_ref: null, override_reason: null, updated_at: '2026-11-05T17:00:00Z',
  }];
  return response;
}

test('access panel shows provisioning details and IT retry refreshes the job', async () => {
  user.role = 'it';
  const response = accessTask();
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockImplementation(async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...response, provisioning: [{ ...response.provisioning![0], status: 'pending', attempts: 0 }],
    } });
    return { data: { connector: 'workspace', status: 'pending' } };
  });
  renderTask('access');
  const panel = await screen.findByRole('region', { name: 'Provisioning' });
  expect(within(panel).getByText('dead')).toBeInTheDocument();
  expect(within(panel).getByText('5')).toBeInTheDocument();
  expect(within(panel).getByText('Simulated timeout')).toBeInTheDocument();
  expect(within(panel).getByText('simulated-worker')).toBeInTheDocument();
  expect(within(panel).getByText('External reference')).toBeInTheDocument();
  expect(screen.getByLabelText('Access request ticket')).toBeDisabled();
  fireEvent.click(within(panel).getByRole('button', { name: 'Retry workspace' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith(
    `/onboarding/workflows/${workflowId}/provisioning/workspace/retry/`, {},
  ));
  expect(await within(panel).findByText('pending')).toBeInTheDocument();
  expect(within(panel).getByRole('button', { name: 'Retry workspace' })).toBeDisabled();
});

test('IT override requires a reason, submits it, and unlocks access after refresh', async () => {
  user.role = 'it';
  const response = accessTask();
  vi.mocked(api.get).mockResolvedValue({ data: response });
  vi.mocked(api.post).mockImplementation(async () => {
    vi.mocked(api.get).mockResolvedValue({ data: {
      ...response, provisioning: [{ ...response.provisioning![0], status: 'overridden', override_reason: 'IT approved' }],
    } });
    return { data: { connector: 'workspace', status: 'overridden' } };
  });
  renderTask('access');
  const button = await screen.findByRole('button', { name: 'Override workspace' });
  expect(button).toBeDisabled();
  const reason = screen.getByLabelText('Override reason for workspace');
  fireEvent.change(reason, { target: { value: '   ' } });
  expect(button).toBeDisabled();
  fireEvent.change(reason, { target: { value: '  IT approved  ' } });
  fireEvent.click(button);
  await waitFor(() => expect(api.post).toHaveBeenCalledWith(
    `/onboarding/workflows/${workflowId}/provisioning/workspace/override/`, { reason: 'IT approved' },
  ));
  expect(await screen.findByText('overridden')).toBeInTheDocument();
  expect(screen.getByText('IT approved')).toBeInTheDocument();
  expect(screen.getByLabelText('Access request ticket')).toBeEnabled();
  expect(button).toBeDisabled();
});

test.each(['pending', 'retry', 'running', 'succeeded', 'overridden'] as const)('IT controls follow the job guard for %s', async status => {
  user.role = 'it';
  const response = accessTask();
  response.provisioning![0].status = status;
  vi.mocked(api.get).mockResolvedValue({ data: response });
  renderTask('access');
  expect(await screen.findByRole('button', { name: 'Retry workspace' })).toBeDisabled();
  const input = screen.getByLabelText('Override reason for workspace');
  const button = screen.getByRole('button', { name: 'Override workspace' });
  if (status === 'pending' || status === 'retry') {
    expect(input).toBeEnabled();
    fireEvent.change(input, { target: { value: 'IT approved' } });
    expect(button).toBeEnabled();
  } else {
    expect(input).toBeDisabled();
    expect(button).toBeDisabled();
  }
});

test.each(['hr', 'manager', 'employee'] as const)('%s can see the access panel without IT controls', async role => {
  user.role = role;
  const response = accessTask();
  response.provisioning![0].external_ref = 'simulated-account';
  vi.mocked(api.get).mockResolvedValue({ data: response });
  renderTask('access');
  const panel = await screen.findByRole('region', { name: 'Provisioning' });
  expect(within(panel).getByText('simulated-account')).toBeInTheDocument();
  expect(within(panel).queryByRole('button')).not.toBeInTheDocument();
  expect(within(panel).queryByRole('textbox')).not.toBeInTheDocument();
});

test('job conflict displays refresh guidance and loads the competing running state', async () => {
  user.role = 'it';
  const response = accessTask();
  vi.mocked(api.get).mockResolvedValueOnce({ data: response }).mockResolvedValue({ data: {
    ...response, provisioning: [{ ...response.provisioning![0], status: 'running' }],
  } });
  vi.mocked(api.post).mockRejectedValue({ response: { status: 409 } });
  renderTask('access');
  fireEvent.change(await screen.findByLabelText('Override reason for workspace'), { target: { value: 'IT approved' } });
  fireEvent.click(screen.getByRole('button', { name: 'Override workspace' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Someone else changed this workflow; refresh and try again.');
  expect(await screen.findByText('running')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Override workspace' })).toBeDisabled();
});

test('completed access evidence displays saved job results', async () => {
  const response = accessTask();
  response.step = { ...response.step, state: 'done', completed_at: completion.completion.at,
    evidence: { provisioning: [{ connector: 'workspace', status: 'overridden', external_ref: null,
      override_reason: 'Saved IT approval' }, { connector: 'slack', status: 'succeeded',
      external_ref: 'simulated-slack-account', override_reason: null }] } };
  vi.mocked(api.get).mockResolvedValue({ data: response });
  renderTask('access');
  expect(await screen.findByText('workspace — overridden')).toBeInTheDocument();
  expect(screen.getByText('Override reason: Saved IT approval')).toBeInTheDocument();
  expect(screen.getByText('External reference: simulated-slack-account')).toBeInTheDocument();
});
