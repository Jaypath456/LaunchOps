import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import api from '../../api/axios';
import './student.css';

export default function Catalog() {
  const [courses, setCourses] = useState([]);
  const [nextPageUrl, setNextPageUrl] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [enrolledIds, setEnrolledIds] = useState([]); 
  const [loading, setLoading] = useState(true);
  const [enrolling, setEnrolling] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    Promise.all([
      api.get('/courses/'),
      api.get('/my-courses/') 
    ])
      .then(([resCourses, resEnrolled]) => {
        // /courses/ is paginated ({count, next, previous, results}) since
        // the catalog can grow without bound; /my-courses/ is not (see
        // backend/courses/views.py for why).
        setCourses(resCourses.data.results);
        setNextPageUrl(resCourses.data.next);
        setEnrolledIds(resEnrolled.data.map(c => c.course.id));
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const handleLoadMore = async () => {
    if (!nextPageUrl) return;
    setLoadingMore(true);
    try {
      const res = await api.get(nextPageUrl);
      setCourses(prev => [...prev, ...res.data.results]);
      setNextPageUrl(res.data.next);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingMore(false);
    }
  };

  const handleEnroll = async (courseId) => {
    setEnrolling(courseId);
    try {
      await api.post(`/courses/${courseId}/enroll/`);
      navigate(`/student/courses/${courseId}`);
    } catch (err) {
      if (err.response?.data?.error === 'Already enrolled in this course') {
        navigate(`/student/courses/${courseId}`);
      } else {
        alert('Could not enroll. Please try again.');
      }
    } finally {
      setEnrolling(null);
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Browse Courses</h1>
          <p className="text-muted">Find a course to start learning</p>
        </div>
      </div>

      {loading ? (
        <p>Loading courses...</p>
      ) : courses.length === 0 ? (
        <div className="empty-state">
          <p>No courses available right now. Check back soon!</p>
        </div>
      ) : (
        <div className="course-grid">
          {courses.map(course => {
            const isEnrolled = enrolledIds.includes(course.id);
          
            return (
              <div className="course-card" key={course.id}>
                <div className="course-card-instructor">
                  by {course.instructor.username}
                </div>
                <h3>{course.title}</h3>
                <p className="course-description">
                  {course.description || 'No description provided.'}
                </p>
                <div className="course-card-footer">
                  <div className="course-stats">
                    <span> {course.chapter_count} chapters</span>
                    <span> {course.enrolled_count} students</span>
                  </div>
                  <div className="card-btns">
                    <Link to={`/student/courses/${course.id}`} className="btn-outline">
                      Preview
                    </Link>
                    
                    {isEnrolled ? (
                      <button 
                        onClick={() => navigate(`/student/courses/${course.id}`)} 
                        className="btn-primary"
                      >
                        Enter Course
                      </button>
                    ) : (
                      <button
                        onClick={() => handleEnroll(course.id)}
                        className="btn-primary"
                        disabled={enrolling === course.id}
                      >
                        {enrolling === course.id ? 'Joining...' : 'Join Course'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })} 
        </div>
      )}

      {!loading && nextPageUrl && (
        <div className="load-more-row">
          <button
            onClick={handleLoadMore}
            className="btn-outline"
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading...' : 'Load More Courses'}
          </button>
        </div>
      )}
    </div>
  );
}