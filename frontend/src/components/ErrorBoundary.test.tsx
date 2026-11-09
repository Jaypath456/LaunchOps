import React from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import ErrorBoundary from './ErrorBoundary';

function Boom(): React.JSX.Element {
  throw new Error('render failure');
}

afterEach(() => vi.restoreAllMocks());

test('a throwing child shows the fallback and logs the error', () => {
  const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  render(<ErrorBoundary resetKey="/a"><Boom /></ErrorBoundary>);
  expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  expect(log.mock.calls.some(call => call[0] === 'Unhandled render error')).toBe(true);
});

test('the fallback clears when resetKey changes', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const { rerender } = render(<ErrorBoundary resetKey="/a"><Boom /></ErrorBoundary>);
  expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  rerender(<ErrorBoundary resetKey="/b"><p>fine</p></ErrorBoundary>);
  expect(screen.getByText('fine')).toBeInTheDocument();
});
