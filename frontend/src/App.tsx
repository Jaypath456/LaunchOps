import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Navbar from './components/Navbar';
import ErrorBoundary from './components/ErrorBoundary';

import Login from './pages/auth/Login';
import Landing from './pages/auth/Landing';
import Workspace from './pages/onboarding/Workspace';
import TaskDetail from './pages/onboarding/TaskDetail';
import './App.css';

function AppRoutes(): React.JSX.Element {
  const { user } = useAuth();

  return (
    <Routes>
      <Route path="/" element={user ? <Navigate to="/workspace" replace /> : <Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/login/:role" element={<Login />} />
      <Route path="/workspace" element={<ProtectedRoute><Workspace /></ProtectedRoute>} />
      <Route path="/workspace/workflows/:id/tasks/:key" element={<ProtectedRoute><TaskDetail /></ProtectedRoute>} />

      <Route path="*" element={<div className="page-container"><h1>Page not found</h1><a href="/">Return to your workspace</a></div>} />
    </Routes>
  );
}

function RoutedApp(): React.JSX.Element {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary resetKey={pathname}>
      <AppRoutes />
    </ErrorBoundary>
  );
}

export default function App(): React.JSX.Element {
  return (
    <BrowserRouter>
      <AuthProvider>
        {/* Global UI Components rendered outside of Routes */}
        <Navbar />
        <main>
          <RoutedApp />
        </main>
      </AuthProvider>
    </BrowserRouter>
  );
}
