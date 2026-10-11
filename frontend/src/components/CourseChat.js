import React, { useEffect, useRef, useState } from 'react';
import api from '../api/axios';
import { ReconnectingSocket } from '../api/ws';
import './CourseChat.css';

// Shared by both instructor and student course pages - the chat room
// is symmetric (one room per course, same UI for everyone in it), so
// there's exactly one component rather than separate Instructor/Student
// versions.
export default function CourseChat({ courseId, chatOpen = true }) {
  const [messages, setMessages] = useState([]);
  const [nextPageUrl, setNextPageUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const socketRef = useRef(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    let isMounted = true;

    api.get(`/courses/${courseId}/messages/`).then(res => {
      if (!isMounted) return;
      // History is newest-first from the API; reverse for the usual
      // chat layout (oldest at top, newest at bottom).
      setMessages([...res.data.results].reverse());
      setNextPageUrl(res.data.next);
    }).catch(err => console.error(err)).finally(() => {
      if (isMounted) setLoading(false);
    });

    const socket = new ReconnectingSocket(`/ws/chat/${courseId}/`, {
      onMessage: (event) => {
        const data = JSON.parse(event.data);
        if (data.error) {
          setError(data.error);
          return;
        }
        setError('');
        setMessages(prev => [...prev, data]);
      },
      onError: () => setError('Connection lost. Reconnecting...'),
    });
    socketRef.current = socket;

    return () => {
      isMounted = false;
      socket.close();
    };
  }, [courseId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleLoadOlder = async () => {
    if (!nextPageUrl) return;
    try {
      const res = await api.get(nextPageUrl);
      setMessages(prev => [...[...res.data.results].reverse(), ...prev]);
      setNextPageUrl(res.data.next);
    } catch (err) {
      console.error(err);
    }
  };

  const handleSend = (e) => {
    e.preventDefault();
    if (!draft.trim() || !chatOpen) return;
    socketRef.current?.send({ body: draft.trim() });
    setDraft('');
  };

  return (
    <div className="course-chat">
      <h2 className="section-title">Class Chat</h2>

      {!chatOpen && (
        <div className="chat-closed-banner">
          This course's term has ended - chat is now read-only.
        </div>
      )}

      {error && <div className="chat-error">{error}</div>}

      <div className="chat-window">
        {nextPageUrl && (
          <button type="button" className="btn-sm chat-load-older" onClick={handleLoadOlder}>
            Load older messages
          </button>
        )}

        {loading ? (
          <p className="text-muted">Loading chat...</p>
        ) : messages.length === 0 ? (
          <p className="text-muted">No messages yet. Say hello!</p>
        ) : (
          messages.map((message, index) => (
            <div className="chat-message" key={message.id ?? index}>
              <span className="chat-message-sender">{message.sender_username}</span>
              <span className="chat-message-body">{message.body}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSend} className="chat-input-row">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={chatOpen ? 'Type a message...' : 'Chat is read-only for this course'}
          disabled={!chatOpen}
        />
        <button type="submit" className="btn-primary" disabled={!chatOpen || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
