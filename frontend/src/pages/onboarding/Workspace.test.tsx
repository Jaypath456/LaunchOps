import { fireEvent, render, screen, cleanup, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import Workspace from './Workspace';
import api from '../../api/axios';
import type { User } from '../../types/auth';
import type { Workflow } from '../../types/onboarding';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user }) }));
const user: User = { username: 'demo_it', role: 'it' };
const workflow = (label: string): Workflow => ({
  id: label.padStart(24, '0'), employee: `employee_${label}`, manager: 'manager', job_title: 'Engineer',
  department: 'Demo', start_date: '2026-10-28', date_needs_correction: false,
  created_at: '2026-10-01T17:00:00Z', status: 'active', steps: [], events: [],
});
const pageData = (rows: Workflow[], page = 1) => ({ count: 106, page, total_pages: 9, results: rows });
function mockTimeline(url: string | undefined) {
  if (!url?.endsWith('/timeline/')) return null;
  return { data: { id: url.split('/')[3], events: [] } };
}
beforeEach(() => { user.username = 'demo_it'; user.role = 'it'; });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

test('navigation requests pages beyond 100 and preserves browser history', async () => {
  vi.mocked(api.get).mockImplementation(async url => {
    const timeline = mockTimeline(url);
    if (timeline) return timeline;
    const page = Number(new URL(url!, 'http://local').searchParams.get('page'));
    return { data: pageData([workflow(page === 1 ? '106' : '1')], page) };
  });
  render(<MemoryRouter initialEntries={['/workspace?page=9']}><Workspace /></MemoryRouter>);
  expect(await screen.findByText('employee_1', { selector: 'strong' })).toBeInTheDocument();
  expect(screen.getByText('Page 9 of 9')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/onboarding/workflows/?page=8'));
});

test('a stale response cannot replace the currently requested page', async () => {
  let release!: (value: unknown) => void;
  const stale = new Promise(resolve => { release = resolve; });
  let calls = 0;
  vi.mocked(api.get).mockImplementation(async url => {
    const timeline = mockTimeline(url);
    if (timeline) return timeline;
    calls += 1;
    if (calls === 2) return stale as never;
    return { data: pageData([workflow(calls === 1 ? '106' : '1')]) };
  });
  render(<MemoryRouter><Workspace /></MemoryRouter>);
  await screen.findByText('Page 1 of 9');
  // Start an older background refresh, then navigate while it is in flight.
  fireEvent.focus(window);
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('employee_1', { selector: 'strong' });
  release({ data: pageData([workflow('999')]) });
  await waitFor(() => expect(screen.queryByText('employee_999')).not.toBeInTheDocument());
  expect(screen.getByText('Page 2 of 9')).toBeInTheDocument();
});

test('task links use workflow ids and keys, done steps count, and overdue is server supplied', async () => {
  const row = workflow('abc123');
  const fields = { description: 'Prepare accounts', owner_role: 'it' as const, kind: 'record',
    due_at: '2999-01-01T17:00:00Z', prerequisites: [], completion_mode: 'record' };
  row.steps = [
    { ...fields, key: 'access', title: 'Provision access', state: 'ready', completed_at: null, overdue: true },
    { ...fields, key: 'profile', title: 'Review profile', state: 'done', completed_at: '2026-10-01T17:00:00Z', overdue: false },
    { ...fields, key: 'equipment', title: 'Prepare equipment', state: 'ready', completed_at: null, overdue: false, due_at: '2000-01-01T17:00:00Z' },
  ];
  // Workflow events must not be used as the timeline source.
  row.events = [{ id: 0, actor: 'System', step: null, detail: 'Wrong timeline source', action: 'completed', created_at: row.created_at }];
  vi.mocked(api.get).mockImplementation(async url => mockTimeline(url) || { data: pageData([row]) });
  render(<MemoryRouter><Workspace /></MemoryRouter>);
  const links = await screen.findAllByRole('link', { name: /Provision access/ });
  for (const link of links) expect(link).toHaveAttribute('href', `/workspace/workflows/${row.id}/tasks/access`);
  expect(screen.getAllByText('Overdue')).toHaveLength(1);
  expect(screen.getByText('1/3 complete')).toBeInTheDocument();
  expect(within(screen.getByText('PAST DUE').parentElement!).getByText('1')).toBeInTheDocument();
  expect(await screen.findByText('No activity yet.')).toBeInTheDocument();
  expect(screen.queryByText('Wrong timeline source')).not.toBeInTheDocument();
});

test('HR launches with directory string ids and reschedules via PATCH', async () => {
  user.role = 'hr';
  const row = workflow('abc123');
  const employeeId = '000000000000000000aaa111';
  const managerId = '000000000000000000bbb222';
  vi.mocked(api.get).mockImplementation(async url => {
    if (url === '/onboarding/directory/') return { data: { users: [
      { id: employeeId, username: 'new_hire', role: 'employee' },
      { id: managerId, username: 'manager', role: 'manager' },
    ] } };
    return mockTimeline(url) || { data: pageData([row]) };
  });
  vi.mocked(api.post).mockResolvedValue({ data: row });
  vi.mocked(api.patch).mockResolvedValue({ data: row });
  render(<MemoryRouter><Workspace /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: '+ Start onboarding' }));
  await screen.findByRole('option', { name: 'new_hire' });
  fireEvent.change(screen.getByLabelText('New hire'), { target: { value: employeeId } });
  fireEvent.change(screen.getByLabelText('Manager', { exact: true }), { target: { value: managerId } });
  fireEvent.change(screen.getByLabelText('Job title'), { target: { value: 'Engineer' } });
  fireEvent.change(screen.getByLabelText('Department'), { target: { value: 'Engineering' } });
  fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-11-07' } });
  fireEvent.click(screen.getByRole('button', { name: 'Launch workflow →' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/onboarding/workflows/', {
    employee_id: employeeId, manager_id: managerId, job_title: 'Engineer', department: 'Engineering', start_date: '2026-11-07',
  }));
  await screen.findByText('Onboarding started for employee_abc123.');
  fireEvent.click(screen.getByRole('button', { name: 'Correct start date' }));
  fireEvent.change(screen.getByLabelText('Correct start date'), { target: { value: '2026-11-08' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save date' }));
  await waitFor(() => expect(api.patch).toHaveBeenCalledWith(`/onboarding/workflows/${row.id}/`, { start_date: '2026-11-08' }));
  expect(await screen.findByText('Start date and task due dates updated.')).toBeInTheDocument();
});

test('reschedule conflicts give clear refresh guidance', async () => {
  user.role = 'hr';
  vi.mocked(api.get).mockImplementation(async url => {
    if (url === '/onboarding/directory/') return { data: { users: [] } };
    return mockTimeline(url) || { data: pageData([workflow('abc123')]) };
  });
  vi.mocked(api.patch).mockRejectedValue({ response: { status: 409 } });
  render(<MemoryRouter><Workspace /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Correct start date' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save date' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Someone else changed this workflow; refresh and try again.');
});
