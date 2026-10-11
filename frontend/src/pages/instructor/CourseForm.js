import React, { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../../api/axios';
import './instructor.css';

export default function CourseForm() {
  const { courseId } = useParams();
  const isEditing = Boolean(courseId);
  const navigate = useNavigate();

  const [form, setForm] = useState({
    title: '',
    description: '',
    is_published: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (isEditing) {
      api.get(`/courses/${courseId}/`)
        .then(res => {
          setForm({
            title: res.data.title,
            description: res.data.description,
            is_published: res.data.is_published,
          });
        })
        .catch(() => setError('Could not load course'));
    }
  }, [courseId, isEditing]);

  const handleChange = (e) => {
    const val = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm({ ...form, [e.target.name]: val });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      if (isEditing) {
        await api.put(`/courses/${courseId}/`, form);
        navigate(`/instructor/courses/${courseId}`);
      } else {
        const res = await api.post('/courses/create/', form);
        navigate(`/instructor/courses/${res.data.id}`);
      }
    } catch (err) {
      setError('Failed to save course. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>{isEditing ? 'Edit Course' : 'Create New Course'}</h1>
      </div>

      {error && <div className="error-msg">{error}</div>}

      <div className="form-card">
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Course Title *</label>
            <input
              type="text"
              name="title"
              value={form.title}
              onChange={handleChange}
              placeholder="Enter the Course title..."
              required
            />
          </div>

          <div className="form-group">
            <label>Description</label>
            <textarea
              name="description"
              value={form.description}
              onChange={handleChange}
              rows={4}
              placeholder="Enter the description... "
            />
          </div>

          <div className="form-group checkbox-group">
            <label>
              <input
                type="checkbox"
                name="is_published"
                checked={form.is_published}
                onChange={handleChange}
              />
              Publish this course (makes it visible to students)
            </label>
          </div>

          <div className="form-actions">
            <button type="button" onClick={() => navigate(-1)} className="btn-secondary">
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={loading}>
              {loading ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Course'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
