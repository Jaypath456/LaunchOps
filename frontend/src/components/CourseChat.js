import React, { useEffect, useRef, useState } from 'react';
import api from '../api/axios';
import './CourseChat.css';

// Shared by both instructor and student course pages - the chat room
// is symmetric (one room per course, same UI for everyone in it), so
// there's exactly one component rather than separate Instructor/Student
// versions.
export default function CourseChat({ courseId }) {
  const [messages, setMessages] = useState([]);
  const [nextPageUrl, setNextPageUrl] = useState(null);
  const [loading, setLoading] = useState(true);
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


    return () => {
      isMounted = false;
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

  return (
    <div className="course-chat">
      <h2 className="section-title">Class Chat History</h2>

      <div className="chat-closed-banner">Chat history is read-only.</div>

      <div className="chat-window">
        {nextPageUrl && (
          <button type="button" className="btn-sm chat-load-older" onClick={handleLoadOlder}>
            Load older messages
          </button>
        )}

        {loading ? (
          <p className="text-muted">Loading chat history...</p>
        ) : messages.length === 0 ? (
          <p className="text-muted">No saved messages.</p>
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

    </div>
  );
}
