import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../../api/axios';
import PlateEditor, { emptyDocument } from '../../components/PlateEditor';
import './instructor.css';

const OPTION_ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz';

function nextOptionId(options) {
  const usedIds = new Set(options.map((opt) => opt.id));
  for (const letter of OPTION_ID_ALPHABET) {
    if (!usedIds.has(letter)) return letter;
  }
  // Fallback for the unlikely case of >26 options.
  return `opt-${options.length}`;
}

export default function QuestionForm() {
  const { quizId: quizIdParam, questionId } = useParams();
  const isEditing = Boolean(questionId);
  const navigate = useNavigate();

  const [quizId, setQuizId] = useState(quizIdParam);
  const [questionType, setQuestionType] = useState('single_choice');
  const [prompt, setPrompt] = useState(emptyDocument);
  const [options, setOptions] = useState([
    { id: 'a', text: '' },
    { id: 'b', text: '' },
  ]);
  const [correctOptionIds, setCorrectOptionIds] = useState([]);
  const [correctAnswer, setCorrectAnswer] = useState('');
  const [points, setPoints] = useState(1);
  const [orderIndex, setOrderIndex] = useState(0);
  const [timeLimitSeconds, setTimeLimitSeconds] = useState(20);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isEditing) return;
    api.get(`/questions/${questionId}/`).then(res => {
      const q = res.data;
      setQuizId(q.quiz);
      setQuestionType(q.question_type);
      setPoints(q.points);
      setOrderIndex(q.order_index);
      setTimeLimitSeconds(q.time_limit_seconds ?? 20);
      setPrompt(q.body?.prompt || emptyDocument);
      if (q.question_type === 'short_answer') {
        setCorrectAnswer(q.body?.correct_answer || '');
      } else {
        setOptions(q.body?.options || options);
        setCorrectOptionIds(q.body?.correct_option_ids || []);
      }
    }).catch(() => setError('Could not load question.'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionId, isEditing]);

  const handleOptionTextChange = (id, text) => {
    setOptions(options.map(opt => (opt.id === id ? { ...opt, text } : opt)));
  };

  const handleAddOption = () => {
    const id = nextOptionId(options);
    setOptions([...options, { id, text: '' }]);
  };

  const handleRemoveOption = (id) => {
    setOptions(options.filter(opt => opt.id !== id));
    setCorrectOptionIds(correctOptionIds.filter(optId => optId !== id));
  };

  const handleToggleCorrect = (id) => {
    if (questionType === 'single_choice') {
      setCorrectOptionIds([id]);
    } else {
      setCorrectOptionIds(
        correctOptionIds.includes(id)
          ? correctOptionIds.filter(optId => optId !== id)
          : [...correctOptionIds, id]
      );
    }
  };

  const handleTypeChange = (newType) => {
    setQuestionType(newType);
    if (newType === 'single_choice' && correctOptionIds.length > 1) {
      setCorrectOptionIds(correctOptionIds.slice(0, 1));
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    let body;
    if (questionType === 'short_answer') {
      body = { prompt, correct_answer: correctAnswer };
    } else {
      body = { prompt, options, correct_option_ids: correctOptionIds };
    }

    setLoading(true);
    try {
      const payload = {
        question_type: questionType,
        body,
        points: Number(points),
        order_index: Number(orderIndex),
        time_limit_seconds: Number(timeLimitSeconds),
      };
      if (isEditing) {
        await api.put(`/questions/${questionId}/`, payload);
      } else {
        await api.post(`/quizzes/${quizId}/questions/create/`, payload);
      }
      navigate(`/instructor/quizzes/${quizId}`);
    } catch (err) {
      const errors = err.response?.data;
      if (errors && typeof errors === 'object') {
        setError(Object.values(errors).flat().join(' '));
      } else {
        setError('Failed to save question.');
      }
    } finally {
      setLoading(false);
    }
  };

  const isChoiceType = questionType === 'single_choice' || questionType === 'multiple_choice';

  return (
    <div className="page-container">
      <h1>{isEditing ? 'Edit Question' : 'New Question'}</h1>
      {error && <div className="error-msg">{error}</div>}

      <form onSubmit={handleSubmit}>
        <div className="form-card">
          <div className="form-row">
            <div className="form-group" style={{ flex: 1 }}>
              <label>Question Type</label>
              <select value={questionType} onChange={(e) => handleTypeChange(e.target.value)}>
                <option value="single_choice">Single Choice</option>
                <option value="multiple_choice">Multiple Choice</option>
                <option value="short_answer">Short Answer</option>
              </select>
            </div>
            <div className="form-group">
              <label>Points</label>
              <input
                type="number"
                min="0"
                value={points}
                onChange={(e) => setPoints(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Order</label>
              <input
                type="number"
                min="0"
                value={orderIndex}
                onChange={(e) => setOrderIndex(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Time Limit (seconds)</label>
              <input
                type="number"
                min="5"
                max="300"
                value={timeLimitSeconds}
                onChange={(e) => setTimeLimitSeconds(e.target.value)}
                title="How long students have to answer once revealed in a live session"
              />
            </div>
          </div>

          <div className="form-group">
            <label>Prompt</label>
            <PlateEditor value={prompt} onChange={setPrompt} />
          </div>

          {isChoiceType ? (
            <div className="form-group">
              <label>
                Options ({questionType === 'single_choice' ? 'select one correct answer' : 'select all correct answers'})
              </label>
              <div className="option-builder">
                {options.map((opt) => (
                  <div className="option-row" key={opt.id}>
                    <input
                      type={questionType === 'single_choice' ? 'radio' : 'checkbox'}
                      name="correct-option"
                      checked={correctOptionIds.includes(opt.id)}
                      onChange={() => handleToggleCorrect(opt.id)}
                      title="Mark as correct"
                    />
                    <input
                      type="text"
                      value={opt.text}
                      onChange={(e) => handleOptionTextChange(opt.id, e.target.value)}
                      placeholder={`Option ${opt.id}`}
                      className="option-text-input"
                    />
                    <button
                      type="button"
                      className="btn-sm btn-danger"
                      onClick={() => handleRemoveOption(opt.id)}
                      disabled={options.length <= 2}
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
              <button type="button" className="btn-secondary" onClick={handleAddOption}>
                + Add Option
              </button>
            </div>
          ) : (
            <div className="form-group">
              <label>Correct Answer</label>
              <input
                type="text"
                value={correctAnswer}
                onChange={(e) => setCorrectAnswer(e.target.value)}
                placeholder="Expected answer (matched case-insensitively)"
                required
              />
            </div>
          )}
        </div>

        <div className="form-actions">
          <button type="button" onClick={() => navigate(-1)} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={loading} className="btn-primary">
            {loading ? 'Saving...' : 'Save Question'}
          </button>
        </div>
      </form>
    </div>
  );
}
