import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../api/axios';
import './instructor.css';

export default function CourseList() {
  const [courses, setCourses] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchCourses = () => {
    api.get('/courses/mine/')
      .then(res => setCourses(res.data))
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchCourses();
  }, []);

  const handleDelete = async (courseId) => {
    if (!window.confirm('Are you sure you want to delete this course?')) return;
    try {
      await api.delete(`/courses/${courseId}/`);
      fetchCourses();
    } catch (err) {
      alert('Failed to delete course');
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>My Courses</h1>
        <Link to="/instructor/courses/create" className="btn-primary">+ New Course</Link>
      </div>

      {loading ? (
        <p>Loading courses...</p>
      ) : courses.length === 0 ? (
        <div className="empty-state">
          <p>No courses yet. Create one to get started!</p>
          <Link to="/instructor/courses/create" className="btn-primary">Create Course</Link>
        </div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Course</th>
              <th>Status</th>
              <th>Chapters</th>
              <th>Students</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {courses.map(course => (
              <tr key={course.id}>
                <td>
                  <div className="table-course-title">{course.title}</div>
                  <div className="text-muted" style={{fontSize: '12px'}}>{course.description?.slice(0, 60)}{course.description?.length > 60 ? '...' : ''}</div>
                </td>
                <td>
                  <span className={`badge ${course.is_published ? 'badge-published' : 'badge-draft'}`}>
                    {course.is_published ? 'Published' : 'Draft'}
                  </span>
                </td>
                <td>{course.chapter_count}</td>
                <td>{course.enrolled_count}</td>
                <td>
                  <div className="action-btns">
                    <Link to={`/instructor/courses/${course.id}`} className="btn-sm">Manage</Link>
                    <button onClick={() => handleDelete(course.id)} className="btn-sm btn-danger">Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
