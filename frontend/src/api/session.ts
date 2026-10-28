import type { AuthResponse } from '../types/auth';

export const SESSION_ENDED = 'launchops:session-ended';
let generation = 0;
export const sessionGeneration = () => generation;

export function clearSession(reason = '') {
  generation += 1;
  ['access_token', 'refresh_token', 'user'].forEach(key => sessionStorage.removeItem(key));
  window.dispatchEvent(new CustomEvent(SESSION_ENDED, { detail: reason }));
}

export function storeSession(data: AuthResponse) {
  generation += 1;
  sessionStorage.setItem('access_token', data.access);
  sessionStorage.setItem('refresh_token', data.refresh);
  sessionStorage.setItem('user', JSON.stringify(data.user));
}
