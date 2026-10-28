import { vi } from 'vitest';
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosAdapter } from 'axios';

let api: AxiosInstance;
let adapter: ReturnType<typeof vi.fn>;
let clearSession: (reason?: string) => void;
let axiosModule: typeof import('axios');
const ok = (config: InternalAxiosRequestConfig, data: unknown = {}) => ({ config, data, status: 200, statusText: 'OK', headers: {} });
const denied = (config: InternalAxiosRequestConfig, status = 401) => Promise.reject(
  new axiosModule.AxiosError('Rejected', undefined, config, undefined, { ...ok(config), status }));

beforeEach(async () => {
  vi.resetModules();
  sessionStorage.clear();
  axiosModule = await import('axios');
  adapter = vi.fn();
  axiosModule.default.defaults.adapter = adapter as AxiosAdapter;
  api = (await import('./axios')).default;
  clearSession = (await import('./session')).clearSession;
  sessionStorage.setItem('access_token', 'old');
  sessionStorage.setItem('refresh_token', 'refresh');
});

test('concurrent 401s share one refresh and replay with the new token', async () => {
  let refreshCalls = 0;
  adapter.mockImplementation(async config => {
    if (config.url === '/auth/refresh/') {
      refreshCalls += 1;
      expect(config.headers.Authorization).toBeUndefined();
      await new Promise(resolve => setTimeout(resolve, 10));
      return ok(config, { access: 'new' });
    }
    return config.headers.Authorization === 'Bearer new' ? ok(config) : denied(config);
  });
  await Promise.all([api.get('/one/'), api.post('/two/', { evidence: true })]);
  expect(refreshCalls).toBe(1);
  expect(adapter).toHaveBeenCalledTimes(5);
  expect(sessionStorage.getItem('access_token')).toBe('new');
});

test('late stale 401 reuses an already refreshed access token', async () => {
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  adapter.mockImplementation(async config => {
    if (config.url === '/auth/refresh/') return ok(config, { access: 'new' });
    if (config.headers.Authorization === 'Bearer new') return ok(config);
    if (config.url === '/late/') await delayed;
    return denied(config);
  });
  const late = api.get('/late/');
  await api.get('/first/');
  release();
  await late;
  expect(adapter.mock.calls.filter(([config]) => config.url === '/auth/refresh/')).toHaveLength(1);
});

test.each(['invalid refresh', 'missing refresh', 'retry rejected'])('%s ends the session without a retry loop', async scenario => {
  if (scenario === 'missing refresh') sessionStorage.removeItem('refresh_token');
  const ended = vi.fn();
  window.addEventListener('launchops:session-ended', ended);
  adapter.mockImplementation(async config => {
    if (config.url === '/auth/refresh/' && scenario === 'retry rejected') return ok(config, { access: 'new' });
    return denied(config);
  });
  await expect(api.get('/protected/')).rejects.toBeDefined();
  expect(adapter.mock.calls.filter(([config]) => config.url === '/auth/refresh/').length).toBe(scenario === 'missing refresh' ? 0 : 1);
  expect(sessionStorage.getItem('access_token')).toBeNull();
  expect(sessionStorage.getItem('refresh_token')).toBeNull();
  expect(ended).toHaveBeenCalledTimes(1);
  window.removeEventListener('launchops:session-ended', ended);
});

test.each([403, 500, 0])('status %s is never refreshed or replayed', async status => {
  adapter.mockImplementation(config => status ? denied(config, status) : Promise.reject(new Error('Network unavailable')));
  await expect(api.post('/action/', {})).rejects.toBeDefined();
  expect(adapter).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem('access_token')).toBe('old');
});

test.each(['/auth/login/', '/auth/register/', '/auth/refresh/'])('%s failure does not trigger refresh', async url => {
  adapter.mockImplementation(config => denied(config));
  await expect(api.post(url, {})).rejects.toBeDefined();
  expect(adapter).toHaveBeenCalledTimes(1);
  expect(sessionStorage.getItem('access_token')).toBe('old');
});

test('logout during refresh prevents session resurrection and replay', async () => {
  let release!: () => void;
  let started!: () => void;
  const waiting = new Promise<void>(resolve => { started = resolve; });
  const delayed = new Promise<void>(resolve => { release = resolve; });
  adapter.mockImplementation(async config => {
    if (config.url === '/auth/refresh/') { started(); await delayed; return ok(config, { access: 'new' }); }
    return denied(config);
  });
  const request = api.get('/protected/');
  await waiting;
  clearSession();
  release();
  await expect(request).rejects.toThrow('Session changed');
  expect(sessionStorage.getItem('access_token')).toBeNull();
  expect(adapter).toHaveBeenCalledTimes(2);
});
