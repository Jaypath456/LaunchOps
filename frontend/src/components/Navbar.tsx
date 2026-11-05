import React from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './Navbar.css';

export default function Navbar(): React.JSX.Element | null {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const handleLogout = (): void => {
    logout();
    navigate('/login');
  };

  const isActive = (path: string): boolean => location.pathname.startsWith(path);

  if (!user) return null;

  return (
    <nav className="navbar">
      <div className="navbar-left">
        <Link to="/" className="navbar-brand">LaunchOps<span style={{color: '#dc9457'}}>.</span></Link>
        <div className="navbar-links">
          <Link to="/workspace" className={isActive('/workspace') ? 'active' : ''}>Workspace</Link>
        </div>
      </div>

      <div className="navbar-right">
        <span className="user-badge">{user.username}</span>
        <button className="logout-btn" onClick={handleLogout}>Logout</button>
      </div>
    </nav>
  );
}
