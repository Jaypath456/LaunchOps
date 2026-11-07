import { isAxiosError } from 'axios';

export function errorResponse(error: unknown): { status?: number; data?: unknown } | undefined {
  if (isAxiosError<unknown>(error)) return error.response;
  // Preserve response-shaped errors used by callers and test doubles.
  if (typeof error !== 'object' || error === null || !('response' in error)) return undefined;
  const response = error.response;
  if (typeof response !== 'object' || response === null) return undefined;
  return {
    status: 'status' in response && typeof response.status === 'number' ? response.status : undefined,
    data: 'data' in response ? response.data : undefined,
  };
}

export function errorField(data: unknown, key: string): string | undefined {
  if (typeof data !== 'object' || data === null || !(key in data)) return undefined;
  const value: unknown = Reflect.get(data, key);
  return typeof value === 'string' ? value : undefined;
}

export function mutationError(error: unknown): string {
  const response = errorResponse(error);
  if (response?.status === 409) return 'Someone else changed this workflow; refresh and try again.';
  const data = response?.data;
  if (!data) return 'Could not save. Try again.';
  if (typeof data === 'string') return data;
  return Object.entries(Object(data) as Record<string, unknown>)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : value}`).join(' · ');
}
