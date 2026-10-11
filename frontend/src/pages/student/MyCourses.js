import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../api/axios';
import './student.css';
import { useAuth } from '../../context/AuthContext';

export default function MyCourses() {
  const { user } = useAuth();
  const [enrollments, setEnrollments] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/my-courses/')
      .then(res => setEnrollments(res.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page-container"><p>Loading...</p></div>;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>My Learning</h1>
          <p className="text-muted">Courses you've joined</p>
        </div>
        <Link to={user?.role === 'employee' ? '/workspace' : '/student/catalog'} className="btn-outline">{user?.role === 'employee' ? 'Workspace' : 'Browse More'}</Link>
      </div>

      {enrollments.length === 0 ? (
        <div className="empty-state">
          <p>You haven't joined any courses yet.</p>
          <Link to={user?.role === 'employee' ? '/workspace' : '/student/catalog'} className="btn-primary">{user?.role === 'employee' ? 'Workspace' : 'Browse Courses'}</Link>
        </div>
      ) : (
        <div className="enrolled-list">
          {enrollments.map(enrollment => (
            <div className="enrolled-card" key={enrollment.id}>
              <div className="enrolled-info">
                <h3>{enrollment.course.title}</h3>
                <p className="text-muted">
                  by {enrollment.course.instructor.username} &nbsp;·&nbsp;
                  {enrollment.course.chapter_count} chapters
                </p>
                <p className="enrolled-date">
                  Joined {new Date(enrollment.enrolled_at).toLocaleDateString()}
                </p>
              </div>
              <Link to={`/student/courses/${enrollment.course.id}`} className="btn-primary">
                Continue
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
