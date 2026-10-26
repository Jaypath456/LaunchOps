import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import type { UserRole } from '../types/auth';

interface ProtectedRouteProps {
  children: React.ReactNode;
  role?: UserRole | UserRole[];
}

// Redirects to login if not authenticated
// Optionally checks role (role='instructor' or 'student')
export default function ProtectedRoute({ children, role }: ProtectedRouteProps) {
  const { user, loading } = useAuth();

  if (loading) {
    return <div style={{ padding: 40 }}>Loading...</div>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (role && !(Array.isArray(role) ? role.includes(user.role) : user.role === role)) {
    // Wrong role — send them to their actual home
    if (user.role === 'instructor') return <Navigate to="/instructor/dashboard" replace />;
    if (['hr', 'it', 'manager', 'employee'].includes(user.role)) return <Navigate to="/workspace" replace />;
    return <Navigate to="/student/catalog" replace />;
  }

  return children;
}
