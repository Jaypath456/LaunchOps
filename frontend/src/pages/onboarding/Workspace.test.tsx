import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import Workspace from './Workspace';
import api from '../../api/axios';

vi.mock('../../api/axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ user: { username: 'demo_it', role: 'it' } }) }));
const workflow = (id: number) => ({ id, employee: `employee_${id}`, manager: 'manager', job_title: 'Engineer', department: 'Demo', start_date: '2026-10-28', steps: [], events: [] });
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test('navigation requests pages beyond 100 and preserves browser history', async () => {
  vi.mocked(api.get).mockImplementation(async url => {
    const page = Number(new URL(url!, 'http://local').searchParams.get('page'));
    return { data: { count: 106, page, total_pages: 9, results: [workflow(page === 1 ? 106 : 1)] } };
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
  vi.mocked(api.get).mockImplementation(async () => {
    calls += 1;
    if (calls === 2) return stale as never;
    return { data: { count: 106, total_pages: 9, results: [workflow(calls === 1 ? 106 : 1)] } };
  });
  render(<MemoryRouter><Workspace /></MemoryRouter>);
  await screen.findByText('Page 1 of 9');
  // Start an older background refresh, then navigate while it is in flight.
  fireEvent.focus(window);
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await screen.findByText('employee_1', { selector: 'strong' });
  release({ data: { count: 106, total_pages: 9, results: [workflow(999)] } });
  await waitFor(() => expect(screen.queryByText('employee_999')).not.toBeInTheDocument());
  expect(screen.getByText('Page 2 of 9')).toBeInTheDocument();
});
