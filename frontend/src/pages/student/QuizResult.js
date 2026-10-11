import React, { useEffect, useState } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import api from '../../api/axios';
import { useAuth } from '../../context/AuthContext';
import './student.css';

export default function QuizResult() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { quizId } = useParams();
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notSubmitted, setNotSubmitted] = useState(false);
  const [retryError, setRetryError] = useState('');
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState(false);

  const retry = async () => {
    setRetrying(true); setRetryError('');
    try {
      await api.post(`/onboarding/assessments/${quizId}/retry/`);
      navigate(`/student/quizzes/${quizId}/take`);
    } catch (err) { setRetryError(err.response?.data?.detail || 'Could not start a new attempt.'); }
    finally { setRetrying(false); }
  };

  useEffect(() => {
    setLoading(true); setNotSubmitted(false); setResult(null); setError('');
    api.get(`/quizzes/${quizId}/my-result/`)
      .then(res => setResult(res.data))
      .catch(err => {
        if (err.response?.status === 404) {
          setNotSubmitted(true);
        } else { setError('Could not load the result. Return to learning and try again.'); }
      })
      .finally(() => setLoading(false));
  }, [quizId]);

  if (loading) return <div className="page-container"><p>Loading result...</p></div>;

  if (notSubmitted) {
    return (
      <div className="page-container">
        <div className="empty-state">
          <p>You haven't taken this quiz yet.</p>
          <Link to={`/student/quizzes/${quizId}/take`} className="btn-primary">Take Quiz</Link>
        </div>
      </div>
    );
  }

  if (error || !result) return <div className="page-container"><p role="alert">{error || 'Result unavailable.'}</p><Link to="/student/my-courses">Back to learning</Link></div>;

  const percentage = result.max_score > 0
    ? Math.round((result.score / result.max_score) * 100)
    : 0;

  return (
    <div className="page-container">
      <div className="quiz-result-card">
        <h1>Quiz Result</h1>
        <div className="quiz-result-score">
          {result.score} / {result.max_score}
        </div>
        <div className="quiz-result-percentage">{percentage}% · {result.passed ? 'Passed' : 'Not passed'}</div>
        <p className="text-muted">
          Submitted {new Date(result.submitted_at).toLocaleString()}
        </p>
      </div>
      {user?.role === 'employee' && !result.passed && <div className="empty-state">
        <p>A 70% score is needed to complete onboarding training. Review the material and try again.</p>
        {retryError && <p role="alert">{retryError}</p>}
        <button className="btn-primary" onClick={retry} disabled={retrying}>{retrying ? 'Preparing…' : 'Retry assessment'}</button>
      </div>}
      <Link to={`/student/courses/${result.course}`} className="btn-outline">Back to lessons and quizzes</Link> <Link to="/workspace" className="btn-outline">Return to onboarding</Link>
    </div>
  );
}
