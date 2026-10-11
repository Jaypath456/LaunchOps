import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import PlateEditor from '../../components/PlateEditor';
import api from '../../api/axios';
import { ReconnectingSocket } from '../../api/ws';
import './student.css';

const DEFAULT_TIME_LIMIT_SECONDS = 20;

// The whole point of this page: the client never decides what to show
// - it renders whatever the server's last event said. 'view' is set
// directly from server events (session.state / question.revealed /
// answer.accepted / session.ended), never inferred client-side.
//
// Deliberately never renders the live chart or leaderboard - those are
// instructor-only (see LiveQuizHost.js). A student's own correctness
// feedback comes from answer.accepted's private is_correct field, not
// from anything broadcast to the room (quizzes/consumers.py).
export default function LiveQuiz() {
  const { roomCode: roomCodeParam } = useParams();
  const navigate = useNavigate();

  const [roomCodeInput, setRoomCodeInput] = useState('');
  const [roomCode, setRoomCode] = useState(roomCodeParam || null);
  const [view, setView] = useState('connecting'); // connecting|waiting|question|result|ended|error
  const [question, setQuestion] = useState(null);
  const [timeLimitSeconds, setTimeLimitSeconds] = useState(DEFAULT_TIME_LIMIT_SECONDS);
  const [revealedAt, setRevealedAt] = useState(null);
  const [remainingSeconds, setRemainingSeconds] = useState(DEFAULT_TIME_LIMIT_SECONDS);
  const [lastAnswerCorrect, setLastAnswerCorrect] = useState(null);
  const [selectedOptions, setSelectedOptions] = useState([]);
  const [shortAnswer, setShortAnswer] = useState('');
  const [error, setError] = useState('');
  const [roomEngine, setRoomEngine] = useState(null);
  const [score, setScore] = useState(null);
  const socketRef = useRef(null);

  useEffect(() => {
    if (!roomCode) return;

    let disposed = false;
    let socket = null;
    setView('connecting');
    api.get(`/sessions/${roomCode}/`).then(({ data }) => {
      if (disposed) return;
      setRoomEngine(data.engine);
      socket = new ReconnectingSocket(`/ws/live/${roomCode}/`, {
      engine: data.engine,
      onOpen: () => setError(''),
      onMessage: (event) => {
        const data = JSON.parse(event.data);
        handleServerEvent(data);
      },
      onError: () => setError('Connection lost. Reconnecting...'),
      onClose: (event) => {
        // 4001/4003/4004 are the backend's application close codes for
        // auth/membership/room failures (see quizzes/consumers.py).
        if ([4001, 4003, 4004].includes(event.code)) {
          setView('error');
          setError('Could not join this room. Check the code and that you are enrolled in the course.');
        }
      },
      });
      socketRef.current = socket;
    }).catch(() => {
      if (!disposed) { setView('error'); setError('Could not find this live room.'); }
    });

    return () => { disposed = true; socket?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomCode]);

  useEffect(() => {
    if (view !== 'ended' || !roomCode || roomEngine !== 'go') return undefined;
    let active = true;
    api.get(`/sessions/${roomCode}/live-results/`)
      .then(({ data }) => { if (active) setScore(data.scores[0] || null); })
      .catch(() => { if (active) setError('Could not load the saved result. Try refreshing.'); });
    return () => { active = false; };
  }, [view, roomCode, roomEngine]);

  // Ticks the countdown while a question is open. Recomputes from
  // revealed_at (a server timestamp, see quizzes/consumers.py) rather
  // than counting down from a client-side "started now" moment - that's
  // what lets a late joiner or a reconnecting client land on the
  // correct remaining time instead of always getting a fresh full-length
  // timer.
  useEffect(() => {
    if (view !== 'question' || !revealedAt) return undefined;

    const revealedMs = new Date(revealedAt).getTime();
    const tick = () => {
      const elapsedSeconds = (Date.now() - revealedMs) / 1000;
      setRemainingSeconds(Math.max(0, timeLimitSeconds - elapsedSeconds));
    };

    tick();
    const interval = setInterval(tick, 200);
    return () => clearInterval(interval);
  }, [view, revealedAt, timeLimitSeconds]);

  const handleServerEvent = (data) => {
    if (data.error) {
      setError(data.error);
      return;
    }

    switch (data.type) {
      case 'session.state':
        if (data.status === 'ended') {
          setView('ended');
        } else if (data.question) {
          setQuestion(data.question);
          setTimeLimitSeconds(data.question.time_limit_seconds || DEFAULT_TIME_LIMIT_SECONDS);
          setRevealedAt(data.revealed_at);
          // A reconnecting client can't tell from this event alone
          // whether it already answered - the server doesn't track
          // that in this snapshot, so default to the question view;
          // if they already answered, resubmitting is a harmless
          // idempotent no-op (see quizzes/consumers.py).
          setView('question');
          resetAnswerState();
        } else {
          setView('waiting');
        }
        break;
      case 'question.revealed':
        setQuestion(data.question);
        setTimeLimitSeconds(data.question.time_limit_seconds || DEFAULT_TIME_LIMIT_SECONDS);
        setRevealedAt(data.revealed_at);
        resetAnswerState();
        setView('question');
        break;
      case 'answer.accepted':
        setLastAnswerCorrect(data.is_correct);
        setView('result');
        break;
      case 'session.ended':
        setView('ended');
        break;
      default:
        // chart.update / leaderboard.update are also delivered here
        // (they're broadcast room-wide, including to students) but are
        // instructor-only display concerns - see LiveQuizHost.js -
        // deliberately ignored on this page.
        break;
    }
  };

  const resetAnswerState = () => {
    setSelectedOptions([]);
    setShortAnswer('');
  };

  const handleJoinSubmit = (e) => {
    e.preventDefault();
    const trimmed = roomCodeInput.trim().toUpperCase();
    if (!trimmed) return;
    navigate(`/student/live/${trimmed}`);
    setRoomCode(trimmed);
  };

  const handleToggleOption = (optionId) => {
    if (question.question_type === 'single_choice') {
      setSelectedOptions([optionId]);
    } else {
      setSelectedOptions(prev =>
        prev.includes(optionId) ? prev.filter(id => id !== optionId) : [...prev, optionId]
      );
    }
  };

  const handleSubmitAnswer = (e) => {
    e.preventDefault();
    const answer = question.question_type === 'short_answer' ? shortAnswer.trim() : selectedOptions;
    socketRef.current?.send({ type: 'answer.submit', question_id: question.id, answer });
  };

  if (!roomCode) {
    return (
      <div className="page-container">
        <div className="form-card" style={{ maxWidth: 360, margin: '40px auto' }}>
          <h1>Join a Live Quiz</h1>
          <form onSubmit={handleJoinSubmit}>
            <div className="form-group">
              <label>Room Code</label>
              <input
                type="text"
                value={roomCodeInput}
                onChange={(e) => setRoomCodeInput(e.target.value)}
                placeholder="e.g. ABC123"
                autoFocus
              />
            </div>
            <button type="submit" className="btn-primary" style={{ width: '100%' }}>
              Join
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="live-quiz-room-badge">Room {roomCode}</div>
      {error && <div className="error-msg">{error}</div>}

      {view === 'connecting' && <p className="text-muted">Connecting...</p>}

      {view === 'waiting' && (
        <div className="live-waiting-screen">
          <div className="live-waiting-spinner" />
          <h2>Waiting for the instructor...</h2>
          <p className="text-muted">The quiz will begin shortly.</p>
        </div>
      )}

      {view === 'question' && question && (
        <div className="form-card">
          <div className="live-timer-bar">
            <div
              className="live-timer-fill"
              style={{ width: `${Math.max(0, (remainingSeconds / timeLimitSeconds) * 100)}%` }}
            />
          </div>
          <div className="live-timer-label">{Math.ceil(remainingSeconds)}s</div>

          <div className="quiz-question-prompt">
            <PlateEditor value={question.body.prompt} readOnly />
          </div>

          {remainingSeconds <= 0 ? (
            <p className="text-muted">Time's up! Waiting for the next question...</p>
          ) : (
            <form onSubmit={handleSubmitAnswer}>
              {question.question_type === 'short_answer' ? (
                <input
                  type="text"
                  className="quiz-short-answer-input"
                  value={shortAnswer}
                  onChange={(e) => setShortAnswer(e.target.value)}
                  placeholder="Your answer"
                  autoFocus
                />
              ) : (
                <div className="quiz-options">
                  {(question.body.options || []).map(option => (
                    <label className="quiz-option-row" key={option.id}>
                      <input
                        type={question.question_type === 'single_choice' ? 'radio' : 'checkbox'}
                        name="live-question-option"
                        checked={selectedOptions.includes(option.id)}
                        onChange={() => handleToggleOption(option.id)}
                      />
                      {option.text}
                    </label>
                  ))}
                </div>
              )}
              <div className="form-actions">
                <button type="submit" className="btn-primary">Submit Answer</button>
              </div>
            </form>
          )}
        </div>
      )}

      {view === 'result' && (
        <div className={`live-result-card ${lastAnswerCorrect ? 'is-correct' : 'is-incorrect'}`}>
          <div className="live-result-icon">{lastAnswerCorrect ? '✓' : '✗'}</div>
          <h2>{lastAnswerCorrect ? 'Correct!' : 'Incorrect'}</h2>
          <p className="text-muted">Waiting for the next question...</p>
        </div>
      )}

      {view === 'ended' && (
        <div className="empty-state">
          <p>This live quiz session has ended. Thanks for playing!</p>
          {score && <p>Your saved live result: {score.score} points across {score.answered} answers.</p>}
        </div>
      )}
    </div>
  );
}
