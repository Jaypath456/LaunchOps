import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import api from '../../api/axios';
import './instructor.css';

export default function Dashboard() {
  const { user } = useAuth();
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/courses/mine/')
      .then(res => setCourses(res.data))
      .catch(err => console.error(err))
      .finally(() => setLoading(false));
  }, []);

  const totalPublished = courses.filter(c => c.is_published).length;
  const totalStudents = courses.reduce((sum, c) => sum + c.enrolled_count, 0);

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Welcome back, {user.username}</h1>
          <p className="text-muted">Here's what's happening with your courses</p>
        </div>
        <Link to="/instructor/courses/create" className="btn-primary">
          + New Course
        </Link>
      </div>

      <div className="stats-row">
        <div className="stat-card">
          <div className="stat-number">{courses.length}</div>
          <div className="stat-label">Total Courses</div>
        </div>
        <div className="stat-card">
          <div className="stat-number">{totalPublished}</div>
          <div className="stat-label">Published</div>
        </div>
        <div className="stat-card">
          <div className="stat-number">{totalStudents}</div>
          <div className="stat-label">Total Students</div>
        </div>
      </div>

      <h2 className="section-title">Your Courses</h2>

      {loading ? (
        <p>Loading...</p>
      ) : courses.length === 0 ? (
        <div className="empty-state">
          <p>You haven't created any courses yet.</p>
          <Link to="/instructor/courses/create" className="btn-primary">Create your first course</Link>
        </div>
      ) : (
        <div className="course-grid">
          {courses.map(course => (
            <div className="course-card" key={course.id}>
              <div className="course-card-header">
                <span className={`badge ${course.is_published ? 'badge-published' : 'badge-draft'}`}>
                  {course.is_published ? 'Published' : 'Draft'}
                </span>
              </div>
              <h3>{course.title}</h3>
              <p className="text-muted">{course.description || 'No description yet'}</p>
              <div className="course-meta">
                <span>{course.chapter_count} chapters</span>
                <span>{course.enrolled_count} students</span>
              </div>
              <div className="card-actions">
                <Link to={`/instructor/courses/${course.id}`} className="btn-secondary">
                  Manage
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
