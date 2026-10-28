import React, { useState } from 'react';
import { useNavigate, Link, useParams } from 'react-router-dom';
import { ROLE_ENTRIES } from './Landing';
import { useAuth } from '../../context/AuthContext';
import { errorResponse, errorField } from '../../types/api';
import './auth.css';

interface LoginForm {
  username: string;
  password: string;
}

export default function Login(): React.JSX.Element {
  const [form, setForm] = useState<LoginForm>({ username: '', password: '' });
  const [error, setError] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const { role } = useParams();
  const entry = ROLE_ENTRIES.find(entry => entry.role === role);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [e.target.name]: e.target.value });
  };
const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const user = await login(form.username.trim(), form.password, entry?.role);
      
      if (['hr', 'it', 'manager', 'employee'].includes(user.role)) {
        navigate('/workspace');
      } else if (user.role === 'instructor') {
        navigate('/instructor/dashboard');
      } else {
        navigate('/student/catalog');
      }
      
    } catch (err) {
      setLoading(false);
      setError(errorField(errorResponse(err)?.data, 'error') || 'Login failed');
      
      setForm((prev) => ({
        ...prev,
        password: ''
      }));
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1 className="auth-title">LaunchOps<span style={{color:'#dd985b'}}>.</span></h1>
        <p className="auth-subtitle">{entry ? `${entry.label} sign in · ${entry.description}` : 'Sign in with your existing account.'}</p>
        <Link className="auth-back" to="/">← Choose your role</Link>

        {error && <div role="alert" className="auth-error">{error}</div>}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="form-group">
            <label htmlFor="login-username">Username</label>
            <input
              id="login-username"
              autoComplete="username"
              type="text"
              name="username"
              value={form.username}
              onChange={handleChange}
              placeholder="Enter your username"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="login-password">Password</label>
            <input
              id="login-password"
              autoComplete="current-password"
              type="password"
              name="password"
              value={form.password}
              onChange={handleChange}
              placeholder="Enter your password"
              required
            />
          </div>
          <button type="submit" className="btn-primary" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <p className="auth-link">
          Don't have an account? <Link to="/register">Register here</Link>
        </p>
      </div>
    </div>
  );
}
