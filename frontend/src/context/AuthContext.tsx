import React, { createContext, useContext, useState, useEffect } from 'react';
import api from '../api/axios';
import type { AuthResponse, RegisterRequest, User } from '../types/auth';

interface AuthProviderProps {
  children: React.ReactNode;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  register: (data: RegisterRequest) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);
const AUTH_STORAGE_KEYS = ['access_token', 'refresh_token', 'user'];

const clearStoredAuth = () => {
  AUTH_STORAGE_KEYS.forEach((key) => {
    sessionStorage.removeItem(key);
  });
};

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    const loadUser = async () => {
      if (!sessionStorage.getItem('access_token')) {
        setLoading(false);
        return;
      }

      try {
        const res = await api.get<User>('/auth/me/');
        sessionStorage.setItem('user', JSON.stringify(res.data));
        setUser(res.data);
      } catch (err) {
        clearStoredAuth();
        setUser(null);
      } finally {
        setLoading(false);
      }
    };

    loadUser();
  }, []);

  const login = async (username: string, password: string): Promise<User> => {
    const res = await api.post<AuthResponse>('/auth/login/', { username, password });
    sessionStorage.setItem('access_token', res.data.access);
    sessionStorage.setItem('refresh_token', res.data.refresh);
    sessionStorage.setItem('user', JSON.stringify(res.data.user));
    setUser(res.data.user);
    return res.data.user;
  };

  const register = async (data: RegisterRequest): Promise<User> => {
    const res = await api.post<AuthResponse>('/auth/register/', data);
    sessionStorage.setItem('access_token', res.data.access);
    sessionStorage.setItem('refresh_token', res.data.refresh);
    sessionStorage.setItem('user', JSON.stringify(res.data.user));
    setUser(res.data.user);
    return res.data.user;
  };

  const logout = () => {
    clearStoredAuth();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  // All consumers are rendered inside AuthProvider in App.
  return useContext(AuthContext)!;
}
