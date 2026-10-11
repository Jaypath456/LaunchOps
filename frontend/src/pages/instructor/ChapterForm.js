import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../../api/axios';
import PlateEditor, { emptyDocument } from '../../components/PlateEditor';
import './instructor.css';

const saveDraft = (data) => sessionStorage.setItem('chapter_draft', JSON.stringify(data));
const getDraft = () => JSON.parse(sessionStorage.getItem('chapter_draft'));
const clearDraft = () => sessionStorage.removeItem('chapter_draft');

export default function ChapterForm() {
  const { courseId, chapterId } = useParams();
  const isEditing = Boolean(chapterId);
  const navigate = useNavigate();

  const [form, setForm] = useState({ 
    title: '', 
    visibility: 'private', 
    order_index: 0 
  });
  
  const [content, setContent] = useState(emptyDocument);
  const [isDirty, setIsDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isEditing) {
      api.get(`/chapters/${chapterId}/`).then(res => {
        setForm({ 
          title: res.data.title, 
          visibility: res.data.visibility, 
          order_index: res.data.order_index 
        });
        setContent(res.data.content || emptyDocument);
      }).catch(() => setError('Failed to load chapter.'));
    } else {
      const draft = getDraft();
      if (draft && window.confirm("Restore your saved progress?")) {
        setForm(draft.form);
        setContent(draft.content);
      }
    }
  }, [chapterId, isEditing]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    
    if (!form.title) {
      if (window.confirm("Form is incomplete. Save as draft?")) {
        saveDraft({ form, content });
        navigate(-1);
      }
      return;
    }

    setLoading(true);
    try {
      const payload = { ...form, content };
      if (isEditing) await api.put(`/chapters/${chapterId}/`, payload);
      else await api.post(`/courses/${courseId}/chapters/create/`, payload);
      
      clearDraft();
      navigate(-1);
    } catch (err) {
      setError('Failed to save.');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = () => {
    if (isDirty && window.confirm('Leave without saving?')) {
      clearDraft();
      navigate(-1);
    } else if (!isDirty) {
      navigate(-1);
    }
  };

  return (
    <div className="page-container">
      <h1>{isEditing ? 'Edit Chapter' : 'New Chapter'}</h1>
      {error && <div className="error-msg">{error}</div>}
      
      <form onSubmit={handleSubmit}>
        <div className="form-card">
          <div className="form-group">
            <label>Title</label>
            <input 
              name="title" 
              value={form.title} 
              onChange={(e) => { setForm({...form, title: e.target.value}); setIsDirty(true); }} 
              placeholder="Chapter Title" 
              required 
            />
          </div>

          <div className="form-group">
            <label>Visibility</label>
            <select 
              name="visibility" 
              value={form.visibility} 
              onChange={(e) => { setForm({...form, visibility: e.target.value}); setIsDirty(true); }}
            >
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
          </div>

          <div className="form-group">
            <label>Content</label>
            <PlateEditor 
              value={content} 
              onChange={(c) => { setContent(c); setIsDirty(true); }} 
            />
          </div>
        </div>

        <div className="form-actions">
          <button type="button" onClick={handleCancel} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={loading} className="btn-primary">
            {loading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  );
}