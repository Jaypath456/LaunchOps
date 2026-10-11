import React, { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import api from '../../api/axios';
import { ReconnectingSocket } from '../../api/ws';
import LiveBarChart from '../../components/LiveBarChart';
import Leaderboard from '../../components/Leaderboard';
import './instructor.css';

export default function LiveQuizHost() {
  const { quizId } = useParams();

  const [session, setSession] = useState(null); // { room_code, status, ... }
  const [question, setQuestion] = useState(null);
  const [questionIndex, setQuestionIndex] = useState(-1);
  const [chartCounts, setChartCounts] = useState({});
  const [correctOptionIds, setCorrectOptionIds] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);
  const [ended, setEnded] = useState(false);
  const [error, setError] = useState('');
  const [savedScores, setSavedScores] = useState([]);
  const [creating, setCreating] = useState(false);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);

  useEffect(() => {
    return () => socketRef.current?.close();
  }, []);

  useEffect(() => {
    if (!ended || session?.engine !== 'go') return undefined;
    let active = true;
    api.get(`/sessions/${session.room_code}/live-results/`)
      .then(({ data }) => { if (active) setSavedScores(data.scores); })
      .catch(() => { if (active) setError('Saved scores could not be loaded.'); });
    return () => { active = false; };
  }, [ended, session?.engine, session?.room_code]);

  const connectSocket = (roomCode, engine) => {
    setConnected(false);
    const socket = new ReconnectingSocket(`/ws/live/${roomCode}/`, {
      engine,
      onOpen: () => { setConnected(true); setError(''); },
      onClose: () => setConnected(false),
      onMessage: (event) => {
        const data = JSON.parse(event.data);
        handleServerEvent(data);
      },
      onError: () => setError('Connection lost. Reconnecting...'),
    });
    socketRef.current = socket;
  };

  const handleServerEvent = (data) => {
    if (data.error) {
      setError(data.error);
      return;
    }
    switch (data.type) {
      case 'session.state':
        setEnded(data.status === 'ended');
        setQuestionIndex(data.question_index);
        if (data.leaderboard) setLeaderboard(data.leaderboard);
        if (data.question) {
          setQuestion(data.question);
          setChartCounts(data.chart || {});
          // Host-only fields (quizzes/consumers.py::_answer_key_for),
          // merged directly into this same message - never present for
          // a student's copy of session.state.
          setCorrectOptionIds(data.correct_option_ids || []);
        }
        break;
      case 'question.revealed':
        setQuestion(data.question);
        setQuestionIndex(data.question_index);
        setChartCounts({});
        // Same host-only merge as above, but on the question.revealed
        // broadcast (see broadcast_question_revealed's self.is_host
        // check) - this is exactly why it's safe to read here
        // unconditionally: a student's browser never receives this key
        // at all, so there's nothing to accidentally leak client-side.
        setCorrectOptionIds(data.correct_option_ids || []);
        setError('');
        break;
      case 'chart.update':
        setChartCounts(data.counts);
        break;
      case 'leaderboard.update':
        setLeaderboard(data.rankings);
        break;
      case 'session.ended':
        setEnded(true);
        break;
      default:
        break;
    }
  };

  const handleCreateSession = async () => {
    setCreating(true);
    setError('');
    try {
      const res = await api.post(`/quizzes/${quizId}/sessions/`);
      setSession(res.data);
    } catch (err) {
      setError('Could not create a live session for this quiz.');
    } finally {
      setCreating(false);
    }
  };

  const handleStartSession = async () => {
    try {
      const res = await api.post(`/sessions/${session.room_code}/start/`);
      setSession(res.data);
      connectSocket(session.room_code, res.data.engine);
    } catch (err) {
      setError('Could not start the session.');
    }
  };

  const handleAdvance = () => {
    socketRef.current?.send({ type: 'question.advance' });
  };

  const handleEnd = () => {
    socketRef.current?.send({ type: 'session.end' });
  };

  const totalResponses = Object.values(chartCounts).reduce((sum, n) => sum + Number(n), 0);

  if (!session) {
    return (
      <div className="page-container">
        <h1>Go Live</h1>
        <p className="text-muted">Start a live (Mentimeter-style) session for this quiz.</p>
        {error && <div className="error-msg">{error}</div>}
        <button className="btn-primary" onClick={handleCreateSession} disabled={creating}>
          {creating ? 'Creating...' : 'Create Live Session'}
        </button>
      </div>
    );
  }

  if (session.status === 'lobby') {
    return (
      <div className="page-container">
        <h1>Session Created</h1>
        <div className="live-room-code-display">{session.room_code}</div>
        <p className="text-muted">
          Share this code with your students. They can join at /student/live/{session.room_code}.
        </p>
        {error && <div className="error-msg">{error}</div>}
        <button className="btn-primary" onClick={handleStartSession}>
          Start Session
        </button>
      </div>
    );
  }

  if (ended) {
    return (
      <div className="page-container">
        <div className="empty-state">
          <p>Session {session.room_code} has ended.</p>
          {savedScores.length > 0 && <>
            <h2>Saved live results</h2>
            <ol>{savedScores.map(row => <li key={row.user_id}>{row.username}: {row.score} points ({row.answered} answers)</li>)}</ol>
          </>}
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="live-quiz-room-badge">Room {session.room_code} · Live</div>
      {error && <div className="error-msg">{error}</div>}

      {!question ? (
        <div className="form-card">
          <p className="text-muted">No question revealed yet.</p>
          <button className="btn-primary" onClick={handleAdvance} disabled={!connected}>
            Reveal First Question
          </button>
        </div>
      ) : (
        <div className="form-card">
          <div className="chapter-meta" style={{ marginBottom: 10 }}>
            <span className="badge badge-private">Question {questionIndex + 1}</span>
            <span className="text-muted" style={{ marginLeft: 8 }}>{totalResponses} response(s)</span>
          </div>
          <LiveBarChart question={question} counts={chartCounts} correctOptionIds={correctOptionIds} />
          <div className="form-actions">
            <button className="btn-primary" onClick={handleAdvance} disabled={!connected}>
              Next Question
            </button>
          </div>
        </div>
      )}

      <div className="form-card">
        <Leaderboard rankings={leaderboard} />
      </div>

      <div className="form-actions" style={{ marginTop: 20 }}>
        <button className="btn-secondary btn-danger" onClick={handleEnd} disabled={!connected}>
          End Session
        </button>
      </div>
    </div>
  );
}
