import axios from 'axios';
import type { AxiosInstance, InternalAxiosRequestConfig } from 'axios';
import { clearSession, sessionGeneration } from './session';

const api: AxiosInstance = axios.create({
  baseURL: (import.meta.env.REACT_APP_API_URL as string | undefined) || 'http://127.0.0.1:8000/api',
});

interface SessionRequest extends InternalAxiosRequestConfig {
  _retried?: boolean;
  _generation?: number;
  _access?: string | null;
}

// A separate client prevents refresh recursion and sends no expired bearer token.
const refreshApi = axios.create({ baseURL: api.defaults.baseURL });
let pending: { generation: number; promise: Promise<string> } | null = null;
const publicAuth = (url = '') => /\/auth\/(login|refresh)\/?(?:\?|$)/.test(url);

api.interceptors.request.use((config: SessionRequest) => {

  const token = sessionStorage.getItem('access_token');
  config._generation = sessionGeneration();
  config._access = token;
  if (token && !publicAuth(config.url)) {
    config.headers.Authorization = `Bearer ${token}`;
  } else {
    delete config.headers.Authorization;
  }
  return config;
});

api.interceptors.response.use(response => response, async error => {
  const config = error.config as SessionRequest | undefined;
  if (!config || error.response?.status !== 401 || publicAuth(config.url)) return Promise.reject(error);
  const generation = config._generation;
  if (generation !== sessionGeneration()) return Promise.reject(error);
  const fail = () => {
    if (generation === sessionGeneration()) clearSession('Your session expired. Please sign in again.');
  };
  if (config._retried) { fail(); return Promise.reject(error); }
  config._retried = true;
  try {
    let token = sessionStorage.getItem('access_token');
    // A late 401 may arrive after a concurrent refresh already succeeded.
    if (!token || token === config._access) {
      const refresh = sessionStorage.getItem('refresh_token');
      if (!refresh) throw error;
      if (!pending || pending.generation !== generation) {
        const promise = refreshApi.post<{ access: string; refresh?: string }>('/auth/refresh/', { refresh }).then(({ data }) => {
          if (sessionGeneration() !== generation) throw new Error('Session changed during refresh.');
          if (!data.access) throw new Error('No access token returned.');
          sessionStorage.setItem('access_token', data.access);
          if (data.refresh) sessionStorage.setItem('refresh_token', data.refresh);
          return data.access;
        });
        const entry = { generation: generation!, promise };
        pending = entry;
        void promise.finally(() => { if (pending === entry) pending = null; }).catch(() => {});
      }
      token = await pending.promise;
    }
    if (sessionGeneration() !== generation) throw new Error('Session changed before retry.');
    config.headers.Authorization = `Bearer ${token}`;
    return await api.request(config);
  } catch (refreshError) { fail(); return Promise.reject(refreshError); }
});

export default api;
