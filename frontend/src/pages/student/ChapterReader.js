import React, { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import { useAuth } from '../../context/AuthContext';
import PlateEditor from '../../components/PlateEditor';
import './student.css';

export default function ChapterReader() {
  const { chapterId } = useParams();
  const { user } = useAuth();
  const [chapters, setChapters] = useState([]);
  const [reviewed, setReviewed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const [linked, setLinked] = useState(false);
  const [chapter, setChapter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true); setError(''); setReviewed(false); setLinked(false); setReviewError('');
    api.get(`/chapters/${chapterId}/`)
      .then(async res => {
        setChapter(res.data);
        const list = await api.get(`/courses/${res.data.course}/chapters/`);
        setChapters(list.data);
        if (user?.role === 'employee') {
          try { const review = await api.get(`/onboarding/lessons/${chapterId}/review/`); setReviewed(review.data.reviewed); setLinked(true); }
          catch (err) { if (err.response?.status !== 404) setReviewError('Could not load lesson progress. Reload to retry.'); }
        }
      })
      .catch(err => {
        if (err.response?.status === 403) {
          setError('This chapter is private or you have not joined the course yet.');
        } else {
          setError('Could not load chapter.');
        }
      })
      .finally(() => setLoading(false));
  }, [chapterId, user?.role]);
  const acknowledge = async () => {
    setSaving(true); setReviewError('');
    try { await api.post(`/onboarding/lessons/${chapterId}/review/`, { acknowledged: true }); setReviewed(true); }
    catch (err) { setReviewError('Could not save acknowledgment. Please try again.'); }
    finally { setSaving(false); }
  };
  const index = chapters.findIndex(c => c.id === Number(chapterId));

  if (loading) return <div className="page-container"><p>Loading chapter...</p></div>;

  if (error) {
    return (
      <div className="page-container">
        <div className="error-msg">{error}</div>
        <Link to="/student/my-courses" className="btn-outline">Back to my courses</Link>
      </div>
    );
  }

  return (
    <div className="reader-page">
      <div className="reader-header">
        <Link to={`/student/courses/${chapter.course}`} className="back-link">← Back to lessons and quizzes</Link>
        <h1>{chapter.title}</h1>
      </div>

      <div className="reader-content">
        {chapter.content && chapter.content.length > 0 ? (
          <PlateEditor value={chapter.content} readOnly={true} />
        ) : (
          <p className="text-muted">This chapter has no content yet.</p>
        )}
      </div>
      {reviewError && <p role="alert">{reviewError}</p>}
      {linked && <div className="form-card"><p>Acknowledgment records your declaration; it does not independently prove reading.</p><button className="btn-primary" disabled={saving || reviewed} onClick={acknowledge}>{reviewed ? 'Lesson acknowledged' : 'Acknowledge this lesson'}</button></div>}
      <nav aria-label="Lesson navigation">{index > 0 && <Link className="btn-outline" to={`/student/chapters/${chapters[index - 1].id}`}>Previous lesson</Link>} {index >= 0 && index < chapters.length - 1 ? <Link className="btn-outline" to={`/student/chapters/${chapters[index + 1].id}`}>Next lesson</Link> : <Link className="btn-outline" to={`/student/courses/${chapter.course}`}>Continue to assessments</Link>}</nav>
    </div>
  );
}
