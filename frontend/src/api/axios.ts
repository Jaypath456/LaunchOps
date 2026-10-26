import axios from 'axios';
import type { AxiosInstance } from 'axios';

const api: AxiosInstance = axios.create({
  baseURL: (import.meta.env.REACT_APP_API_URL as string | undefined) || 'http://127.0.0.1:8000/api',
});

api.interceptors.request.use((config) => {

  const token = sessionStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export default api;
