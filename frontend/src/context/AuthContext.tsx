import React, { createContext, useContext, useState, useEffect } from 'react';
import api from '../api/axios';
import type { AuthResponse, RegisterRequest, User, OnboardingRole } from '../types/auth';
import { clearSession, storeSession, SESSION_ENDED, sessionGeneration } from '../api/session';

interface AuthProviderProps {
  children: React.ReactNode;
}

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string, role?: OnboardingRole) => Promise<User>;
  register: (data: RegisterRequest) => Promise<User>;
  logout: () => void;
  sessionError: string;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [sessionError, setSessionError] = useState('');

  useEffect(() => {
    let active = true;
    const generation = sessionGeneration();
    const ended = (event: Event) => {
      setUser(null);
      setSessionError((event as CustomEvent<string>).detail);
    };
    window.addEventListener(SESSION_ENDED, ended);
    const loadUser = async () => {
      if (!sessionStorage.getItem('access_token') && !sessionStorage.getItem('refresh_token')) {
        setLoading(false);
        return;
      }

      try {
        const res = await api.get<User>('/auth/me/');
        if (!active || generation !== sessionGeneration()) return;
        sessionStorage.setItem('user', JSON.stringify(res.data));
        setUser(res.data);
      } catch (err) {
        if (active && generation === sessionGeneration()) clearSession('Could not restore your session. Please sign in again.');
      } finally {
        if (active) setLoading(false);
      }
    };

    loadUser();
    return () => { active = false; window.removeEventListener(SESSION_ENDED, ended); };
  }, []);

  const login = async (username: string, password: string, role?: OnboardingRole): Promise<User> => {
    const res = await api.post<AuthResponse>('/auth/login/', { username, password, role });
    storeSession(res.data);
    setSessionError('');
    setUser(res.data.user);
    return res.data.user;
  };

  const register = async (data: RegisterRequest): Promise<User> => {
    const res = await api.post<AuthResponse>('/auth/register/', data);
    storeSession(res.data);
    setSessionError('');
    setUser(res.data.user);
    return res.data.user;
  };

  const logout = () => {
    clearSession();
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, sessionError }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
