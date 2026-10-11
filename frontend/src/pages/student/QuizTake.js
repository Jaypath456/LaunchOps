import React, { useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import api from '../../api/axios';
import PlateEditor from '../../components/PlateEditor';
import './student.css';

export default function QuizTake() {
  const { quizId } = useParams();
  const navigate = useNavigate();

  const [quiz, setQuiz] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [answers, setAnswers] = useState({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [alreadySubmitted, setAlreadySubmitted] = useState(false);

  useEffect(() => {
    setLoading(true); setQuiz(null); setAnswers({}); setAlreadySubmitted(false); setError('');
    const load = async () => {
      try {
        // If the student already has a result, send them straight to it
        // instead of letting them fill out the form again - quizzes are
        // one-attempt (see the backend's idempotent submit endpoint).
        const resultRes = await api.get(`/quizzes/${quizId}/my-result/`).catch((err) => {
          if (err.response?.status === 404) return null;
          throw err;
        });
        if (resultRes) {
          setAlreadySubmitted(true);
          setLoading(false);
          return;
        }

        const takeRes = await api.get(`/quizzes/${quizId}/take/`);
        setQuiz(takeRes.data.quiz);
        setQuestions(takeRes.data.questions);
      } catch (err) {
        if (err.response?.status === 403) {
          setError('You must be enrolled in this course to take this quiz.');
        } else {
          setError('Could not load this quiz.');
        }
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [quizId]);

  const handleChoiceAnswer = (questionId, optionId, isSingle) => {
    setAnswers((prev) => {
      if (isSingle) {
        return { ...prev, [questionId]: [optionId] };
      }
      const current = prev[questionId] || [];
      const next = current.includes(optionId)
        ? current.filter((id) => id !== optionId)
        : [...current, optionId];
      return { ...prev, [questionId]: next };
    });
  };

  const handleShortAnswerChange = (questionId, text) => {
    setAnswers((prev) => ({ ...prev, [questionId]: text }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      await api.post(`/quizzes/${quizId}/submit/`, { answers });
      navigate(`/student/quizzes/${quizId}/result`);
    } catch (err) {
      setError('Could not submit your answers. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div className="page-container"><p>Loading quiz...</p></div>;

  if (alreadySubmitted) {
    return <Navigate to={`/student/quizzes/${quizId}/result`} replace />;
  }

  if (error && !quiz) {
    return (
      <div className="page-container">
        <div className="error-msg">{error}</div>
        <Link to="/student/my-courses" className="btn-outline">Back to my courses</Link>
      </div>
    );
  }

  return (
    <div className="page-container">
      <Link to={`/student/courses/${quiz.course}`}>← Back to lessons and quizzes</Link>
      <h1>{quiz.title}</h1>
      {quiz.description && <p className="text-muted">{quiz.description}</p>}
      {error && <div className="error-msg">{error}</div>}

      <form onSubmit={handleSubmit}>
        {questions.map((question, index) => (
          <div className="form-card quiz-question-card" key={question.id}>
            <div className="quiz-question-heading">
              Question {index + 1} <span className="text-muted">({question.points} pts)</span>
            </div>
            <div className="quiz-question-prompt">
              <PlateEditor value={question.body.prompt} readOnly />
            </div>

            {question.question_type === 'short_answer' ? (
              <input
                type="text"
                className="quiz-short-answer-input"
                value={answers[question.id] || ''}
                onChange={(e) => handleShortAnswerChange(question.id, e.target.value)}
                placeholder="Your answer"
              />
            ) : (
              <div className="quiz-options">
                {(question.body.options || []).map((option) => (
                  <label className="quiz-option-row" key={option.id}>
                    <input
                      type={question.question_type === 'single_choice' ? 'radio' : 'checkbox'}
                      name={`question-${question.id}`}
                      checked={(answers[question.id] || []).includes(option.id)}
                      onChange={() => handleChoiceAnswer(
                        question.id, option.id, question.question_type === 'single_choice'
                      )}
                    />
                    {option.text}
                  </label>
                ))}
              </div>
            )}
          </div>
        ))}

        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? 'Submitting...' : 'Submit Quiz'}
          </button>
        </div>
      </form>
    </div>
  );
}
