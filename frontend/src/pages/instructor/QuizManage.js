import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import './instructor.css';

function questionSummary(question) {
  const text = question.body?.prompt?.[0]?.children?.[0]?.text;
  return text || '(no prompt text)';
}

export default function QuizManage() {
  const { quizId } = useParams();
  const [quiz, setQuiz] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [quizRes, questionsRes] = await Promise.all([
        api.get(`/quizzes/${quizId}/`),
        api.get(`/quizzes/${quizId}/questions/`),
      ]);
      setQuiz(quizRes.data);
      setQuestions(questionsRes.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [quizId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleTogglePublish = async () => {
    await api.put(`/quizzes/${quizId}/`, { is_published: !quiz.is_published });
    fetchData();
  };

  const handleDeleteQuestion = async (questionId) => {
    if (!window.confirm('Delete this question?')) return;
    await api.delete(`/questions/${questionId}/`);
    setQuestions(questions.filter(q => q.id !== questionId));
  };

  if (loading) return <div className="page-container"><p>Loading...</p></div>;
  if (!quiz) return <div className="page-container"><p>Quiz not found</p></div>;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <div className="breadcrumb">
            <Link to={`/instructor/courses/${quiz.course}`}>Course</Link> / {quiz.title}
          </div>
          <h1>{quiz.title}</h1>
          <p className="text-muted">{quiz.description || 'No description yet'}</p>
        </div>
        <div className="header-actions">
          <button
            onClick={handleTogglePublish}
            className={quiz.is_published ? 'btn-secondary' : 'btn-primary'}
          >
            {quiz.is_published ? 'Unpublish' : 'Publish'}
          </button>
          <Link to={`/instructor/courses/${quiz.course}/quizzes/${quizId}/edit`} className="btn-secondary">
            Edit Quiz
          </Link>
          <Link to={`/instructor/quizzes/${quizId}/live`} className="btn-primary">
            Go Live
          </Link>
        </div>
      </div>

      <div className="info-bar">
        <span className={`badge ${quiz.is_published ? 'badge-published' : 'badge-draft'}`}>
          {quiz.is_published ? 'Published' : 'Draft'}
        </span>
        <span>{quiz.question_count} question{quiz.question_count === 1 ? '' : 's'}</span>
      </div>

      <div className="section-header">
        <h2>Questions</h2>
        <Link to={`/instructor/quizzes/${quizId}/questions/create`} className="btn-primary">
          + Add Question
        </Link>
      </div>

      {questions.length === 0 ? (
        <div className="empty-state">
          <p>No questions yet. Add your first question to get started.</p>
          <Link to={`/instructor/quizzes/${quizId}/questions/create`} className="btn-primary">
            Add Question
          </Link>
        </div>
      ) : (
        <div className="chapter-list">
          {questions.map((question, index) => (
            <div className="chapter-item" key={question.id}>
              <div className="chapter-left">
                <span className="chapter-number">{index + 1}</span>
                <div>
                  <div className="chapter-title">{questionSummary(question)}</div>
                  <div className="chapter-meta">
                    <span className="badge badge-private">{question.question_type.replace('_', ' ')}</span>
                    <span className="text-muted" style={{ marginLeft: 8 }}>{question.points} pts</span>
                  </div>
                </div>
              </div>
              <div className="chapter-actions">
                <Link to={`/instructor/questions/${question.id}/edit`} className="btn-sm">Edit</Link>
                <button onClick={() => handleDeleteQuestion(question.id)} className="btn-sm btn-danger">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
