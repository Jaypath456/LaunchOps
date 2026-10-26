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
        {['hr', 'it', 'manager', 'employee'].includes(user.role) ? (
          <div className="navbar-links">
            <Link to="/workspace" className={isActive('/workspace') ? 'active' : ''}>Workspace</Link>
            {user.role === 'employee' && <Link to="/student/my-courses">Learning</Link>}
          </div>
        ) : user.role === 'instructor' ? (
          <div className="navbar-links">
            <Link to="/instructor/dashboard" className={isActive('/instructor/dashboard') ? 'active' : ''}>
              Dashboard
            </Link>
            <Link to="/instructor/courses" className={isActive('/instructor/courses') ? 'active' : ''}>
              My Courses
            </Link>
          </div>
        ) : (
          <div className="navbar-links">
            <Link to="/student/catalog" className={isActive('/student/catalog') ? 'active' : ''}>
              Browse Courses
            </Link>
            <Link to="/student/my-courses" className={isActive('/student/my-courses') ? 'active' : ''}>
              My Learning
            </Link>
            <Link to="/student/live" className={isActive('/student/live') ? 'active' : ''}>
              Join Live Quiz
            </Link>
            <Link to="/student/schedule" className={isActive('/student/schedule') ? 'active' : ''}>
              My Schedule
            </Link>
          </div>
        )}
      </div>

      <div className="navbar-right">
        <span className="user-badge">{user.username}</span>
        <button className="logout-btn" onClick={handleLogout}>Logout</button>
      </div>
    </nav>
  );
}
