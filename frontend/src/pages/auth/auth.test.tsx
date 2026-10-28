import { fireEvent, render, screen, cleanup, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { vi } from 'vitest';
import Login from './Login';
import Register from './Register';
import Landing from './Landing';

const { login, register } = vi.hoisted(() => ({ login: vi.fn(), register: vi.fn() }));
vi.mock('../../context/AuthContext', () => ({ useAuth: () => ({ login, register }) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

test('landing exposes all four role login paths', () => {
  render(<MemoryRouter><Landing /></MemoryRouter>);
  for (const [role, label] of [['hr', 'HR'], ['it', 'IT'], ['manager', 'Manager'], ['employee', 'Employee']]) {
    expect(screen.getByRole('link', { name: new RegExp(`Sign in as ${label}`) })).toHaveAttribute('href', `/login/${role}`);
  }
});

test('role login preserves password whitespace and submits the chosen role', async () => {
  login.mockResolvedValue({ role: 'hr' });
  render(<MemoryRouter initialEntries={['/login/hr']}><Routes><Route path="/login/:role" element={<Login />} /><Route path="/workspace" element={<p>Workspace</p>} /></Routes></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' demo_hr ' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: '  exact password  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
  expect(await screen.findByText('Workspace')).toBeInTheDocument();
  expect(login).toHaveBeenCalledWith('demo_hr', '  exact password  ', 'hr');
});

test('registration preserves password whitespace', async () => {
  register.mockResolvedValue({ role: 'employee' });
  render(<MemoryRouter><Register /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'new_hire' } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'hire@example.test' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: '  exact password  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled());
  expect(register).toHaveBeenCalledWith(expect.objectContaining({ password: '  exact password  ' }));
});
