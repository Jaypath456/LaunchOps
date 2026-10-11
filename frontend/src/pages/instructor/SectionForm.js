import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import api from '../../api/axios';
import './instructor.css';

const DAY_OPTIONS = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
];

const emptyMeeting = () => ({ day_of_week: 0, start_time: '09:00', end_time: '09:50' });

export default function SectionForm() {
  const { courseId: courseIdParam, sectionId } = useParams();
  const isEditing = Boolean(sectionId);
  const navigate = useNavigate();

  const [courseId, setCourseId] = useState(courseIdParam);
  const [terms, setTerms] = useState([]);
  const [termId, setTermId] = useState('');
  const [sectionCode, setSectionCode] = useState('');
  const [location, setLocation] = useState('');
  const [capacity, setCapacity] = useState('');
  const [meetings, setMeetings] = useState([emptyMeeting()]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get('/terms/').then(res => {
      setTerms(res.data);
      if (!isEditing && res.data.length > 0) {
        setTermId(res.data[0].id);
      }
    }).catch(() => setError('Could not load terms.'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isEditing) return;
    api.get(`/sections/${sectionId}/`).then(res => {
      const s = res.data;
      setCourseId(s.course);
      setTermId(s.term);
      setSectionCode(s.section_code);
      setLocation(s.location);
      setCapacity(s.capacity ?? '');
      setMeetings(
        s.meetings.length > 0
          ? s.meetings.map(m => ({
              day_of_week: m.day_of_week,
              start_time: m.start_time.slice(0, 5),
              end_time: m.end_time.slice(0, 5),
            }))
          : [emptyMeeting()]
      );
    }).catch(() => setError('Could not load section.'));
  }, [sectionId, isEditing]);

  const handleMeetingChange = (index, field, value) => {
    setMeetings(meetings.map((m, i) => (i === index ? { ...m, [field]: value } : m)));
  };

  const handleAddMeeting = () => setMeetings([...meetings, emptyMeeting()]);

  const handleRemoveMeeting = (index) => setMeetings(meetings.filter((_, i) => i !== index));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const payload = {
        term: termId,
        section_code: sectionCode,
        location,
        capacity: capacity === '' ? null : Number(capacity),
        meetings: meetings.map(m => ({
          day_of_week: Number(m.day_of_week),
          start_time: `${m.start_time}:00`,
          end_time: `${m.end_time}:00`,
        })),
      };

      if (isEditing) {
        await api.put(`/sections/${sectionId}/`, payload);
      } else {
        await api.post(`/courses/${courseId}/sections/create/`, payload);
      }
      navigate(`/instructor/courses/${courseId}/sections`);
    } catch (err) {
      const errors = err.response?.data;
      if (errors && typeof errors === 'object') {
        setError(Object.values(errors).flat().join(' '));
      } else {
        setError('Failed to save section.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-container">
      <h1>{isEditing ? 'Edit Section' : 'New Section'}</h1>
      {error && <div className="error-msg">{error}</div>}

      <form onSubmit={handleSubmit}>
        <div className="form-card">
          <div className="form-row">
            <div className="form-group" style={{ flex: 1 }}>
              <label>Term *</label>
              <select value={termId} onChange={(e) => setTermId(e.target.value)} required>
                <option value="" disabled>Select a term</option>
                {terms.map(term => (
                  <option key={term.id} value={term.id}>{term.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Section Code</label>
              <input
                type="text"
                value={sectionCode}
                onChange={(e) => setSectionCode(e.target.value)}
                placeholder="e.g. LEC 001"
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group" style={{ flex: 1 }}>
              <label>Location</label>
              <input
                type="text"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="e.g. Hoch 114"
              />
            </div>
            <div className="form-group">
              <label>Capacity</label>
              <input
                type="number"
                min="0"
                value={capacity}
                onChange={(e) => setCapacity(e.target.value)}
                placeholder="Optional"
              />
            </div>
          </div>

          <div className="form-group">
            <label>Meeting Times</label>
            <div className="option-builder">
              {meetings.map((meeting, index) => (
                <div className="option-row" key={index}>
                  <select
                    value={meeting.day_of_week}
                    onChange={(e) => handleMeetingChange(index, 'day_of_week', e.target.value)}
                  >
                    {DAY_OPTIONS.map(day => (
                      <option key={day.value} value={day.value}>{day.label}</option>
                    ))}
                  </select>
                  <input
                    type="time"
                    value={meeting.start_time}
                    onChange={(e) => handleMeetingChange(index, 'start_time', e.target.value)}
                    required
                  />
                  <span>to</span>
                  <input
                    type="time"
                    value={meeting.end_time}
                    onChange={(e) => handleMeetingChange(index, 'end_time', e.target.value)}
                    required
                  />
                  <button
                    type="button"
                    className="btn-sm btn-danger"
                    onClick={() => handleRemoveMeeting(index)}
                    disabled={meetings.length <= 1}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
            <button type="button" className="btn-secondary" onClick={handleAddMeeting}>
              + Add Meeting Time
            </button>
          </div>
        </div>

        <div className="form-actions">
          <button type="button" onClick={() => navigate(-1)} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={loading} className="btn-primary">
            {loading ? 'Saving...' : 'Save Section'}
          </button>
        </div>
      </form>
    </div>
  );
}
