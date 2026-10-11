import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import Leaderboard from '../../components/Leaderboard';
import CourseChat from '../../components/CourseChat';
import './student.css';
import { useAuth } from '../../context/AuthContext';

export default function CourseView() {
  const { user } = useAuth();
  const { courseId } = useParams();
  const [course, setCourse] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [quizzes, setQuizzes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isEnrolled, setIsEnrolled] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [leaderboard, setLeaderboard] = useState([]);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [courseRes, chaptersRes, enrollRes, quizzesRes] = await Promise.all([
          api.get(`/courses/${courseId}/`),
          api.get(`/courses/${courseId}/chapters/`),
          api.get(`/courses/${courseId}/enrollment-status/`),
          api.get(`/courses/${courseId}/quizzes/`),
        ]);

        setCourse(courseRes.data);
        setChapters(chaptersRes.data);
        setIsEnrolled(enrollRes.data.enrolled);
        setQuizzes(quizzesRes.data);

        // The persistent (non-live) leaderboard - total quiz score
        // across this course - only enrolled students can see it
        // (see quizzes/views.py::course_leaderboard), so only fetch it
        // once we know enrollment status.
        if (enrollRes.data.enrolled && user?.role === 'student') {
          const leaderboardRes = await api.get(`/courses/${courseId}/leaderboard/`);
          setLeaderboard(leaderboardRes.data);
        }
      } catch (err) {
        console.error("Error loading course:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchAll();
  }, [courseId, user?.role]);

  const handleEnrollmentToggle = async () => {
    setEnrolling(true);
    try {
      if (isEnrolled) {
        await api.delete(`/courses/${courseId}/enroll/`);
        setIsEnrolled(false);
      } else {
        await api.post(`/courses/${courseId}/enroll/`);
        setIsEnrolled(true);
      }
    } catch (err) {
      console.error(err);
      alert('Action failed. Please try again.');
    } finally {
      setEnrolling(false);
    }
  };

  if (loading) return <div className="page-container"><p>Loading course...</p></div>;
  if (!course) return <div className="page-container"><p>Course not found.</p></div>;

  return (
    <div className="page-container">
      <div className="course-hero">
        <div className="breadcrumb">
          <Link to={user?.role === 'employee' ? '/workspace' : '/student/catalog'}>{user?.role === 'employee' ? 'Workspace' : 'Courses'}</Link> / {course.title}
        </div>
        <h1>{course.title}</h1>
        <p className="course-hero-desc">{course.description}</p>
        
        <div className="course-hero-meta">
          <span>Instructor: {course.instructor.username}</span>
          <span>{course.chapter_count} chapters</span>
          <span>{course.enrolled_count} students</span>
        </div>

        {user?.role !== 'employee' && <button 
          onClick={handleEnrollmentToggle} 
          className="btn-primary" 
          disabled={enrolling}
        >
          {enrolling ? 'Processing...' : (isEnrolled ? 'Unenroll' : 'Join This Course')}
        </button>}

        {isEnrolled && (
          <span className="enrolled-tag"> You're enrolled</span>
        )}
      </div>

      <h2 className="chapters-heading">Chapters</h2>

      {chapters.length === 0 ? (
        <p className="text-muted">No public chapters available yet.</p>
      ) : (
        <div className="chapter-list">
          {chapters.map((chapter, index) => (
            <div className="student-chapter-item" key={chapter.id}>
              <div className="chapter-left">
                <span className="chapter-number">{index + 1}</span>
                <div>
                  <div className="chapter-title">{chapter.title}</div>
                </div>
              </div>
              {isEnrolled ? (
                <Link to={`/student/chapters/${chapter.id}`} className="btn-outline">
                  Read
                </Link>
              ) : (
                <span className="locked-msg">Join to read</span>
              )}
            </div>
          ))}
        </div>
      )}

      <h2 className="chapters-heading">Quizzes</h2>

      {quizzes.length === 0 ? (
        <p className="text-muted">No quizzes available yet.</p>
      ) : (
        <div className="chapter-list">
          {quizzes.map((quiz, index) => (
            <div className="student-chapter-item" key={quiz.id}>
              <div className="chapter-left">
                <span className="chapter-number">{index + 1}</span>
                <div>
                  <div className="chapter-title">{quiz.title}</div>
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    {quiz.question_count} question{quiz.question_count === 1 ? '' : 's'}
                  </div>
                </div>
              </div>
              {isEnrolled ? (
                <Link to={`/student/quizzes/${quiz.id}/take`} className="btn-outline">
                  Take Quiz
                </Link>
              ) : (
                <span className="locked-msg">Join to take</span>
              )}
            </div>
          ))}
        </div>
      )}

      {isEnrolled && user?.role === 'student' && (
        <Leaderboard rankings={leaderboard} title="Quiz Leaderboard" />
      )}

      {isEnrolled && user?.role === 'student' && (
        <CourseChat courseId={courseId} chatOpen={course.chat_open} />
      )}
    </div>
  );
}
