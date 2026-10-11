import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../../api/axios';
import WeeklyScheduleCalendar from '../../components/WeeklyScheduleCalendar';
import './student.css';

const DAY_OPTIONS = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
];

export default function ScheduleBuilder() {
  const navigate = useNavigate();

  // Step 1: term + course list
  const [terms, setTerms] = useState([]);
  const [termId, setTermId] = useState('');
  const [availableCourses, setAvailableCourses] = useState([]);
  const [myCourses, setMyCourses] = useState([]);

  // Step 2: breaks
  const [breaks, setBreaks] = useState([]);
  const [newBreak, setNewBreak] = useState({ day_of_week: 0, start_time: '08:00', end_time: '09:00', label: '' });

  // Step 3: generated candidates
  const [candidates, setCandidates] = useState(null);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [savedScheduleId, setSavedScheduleId] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      api.get('/terms/'),
      api.get('/courses/?page_size=100'),
      api.get('/breaks/'),
    ]).then(([termsRes, coursesRes, breaksRes]) => {
      setTerms(termsRes.data);
      if (termsRes.data.length > 0) setTermId(termsRes.data[0].id);
      // /courses/ is paginated ({count, next, previous, results}) once
      // the pagination milestone is in place, but a plain array
      // otherwise - support both so this page works regardless of merge
      // order relative to that milestone.
      const courseData = coursesRes.data;
      setAvailableCourses(Array.isArray(courseData) ? courseData : courseData.results);
      setBreaks(breaksRes.data);
    }).catch(err => console.error(err));
  }, []);

  const courseTitleById = useMemo(() => {
    const map = {};
    myCourses.forEach(c => { map[c.id] = c.title; });
    return map;
  }, [myCourses]);

  const handleAddCourse = (course) => {
    if (myCourses.some(c => c.id === course.id)) return;
    setMyCourses([...myCourses, { id: course.id, title: course.title }]);
    setCandidates(null);
    setSavedScheduleId(null);
  };

  const handleRemoveCourse = (courseId) => {
    setMyCourses(myCourses.filter(c => c.id !== courseId));
    setCandidates(null);
    setSavedScheduleId(null);
  };

  const handleAddBreak = async () => {
    try {
      const res = await api.post('/breaks/', {
        day_of_week: Number(newBreak.day_of_week),
        start_time: `${newBreak.start_time}:00`,
        end_time: `${newBreak.end_time}:00`,
        label: newBreak.label,
      });
      setBreaks([...breaks, res.data]);
      setCandidates(null);
    } catch (err) {
      setError('Could not add break. Check that the end time is after the start time.');
    }
  };

  const handleRemoveBreak = async (breakId) => {
    await api.delete(`/breaks/${breakId}/`);
    setBreaks(breaks.filter(b => b.id !== breakId));
    setCandidates(null);
  };

  const handleGenerate = async () => {
    setError('');
    setGenerating(true);
    setSavedScheduleId(null);
    try {
      const res = await api.post('/schedule/generate/', {
        course_ids: myCourses.map(c => c.id),
        term_id: termId,
      });
      setCandidates(res.data.schedules);
      setCandidateIndex(0);
      if (res.data.schedules.length === 0) {
        setError('No conflict-free schedule was found for this combination of courses and breaks.');
      }
    } catch (err) {
      setError('Could not generate schedules. Please try again.');
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    if (!candidates || candidates.length === 0) return;
    const currentCandidate = candidates[candidateIndex];
    try {
      const res = await api.post('/schedule/saved/', {
        term: termId,
        sections: currentCandidate.map(s => s.id),
      });
      setSavedScheduleId(res.data.id);
    } catch (err) {
      setError('Could not save this schedule.');
    }
  };

  const handleConfirm = async () => {
    if (!savedScheduleId) return;
    setConfirming(true);
    try {
      await api.post(`/schedule/saved/${savedScheduleId}/confirm/`);
      navigate('/student/my-courses');
    } catch (err) {
      setError('Could not confirm this schedule.');
    } finally {
      setConfirming(false);
    }
  };

  const currentCandidate = candidates && candidates.length > 0 ? candidates[candidateIndex] : null;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>Build Your Schedule</h1>
          <p className="text-muted">Add courses and breaks, then generate conflict-free schedule options.</p>
        </div>
      </div>

      {error && <div className="error-msg">{error}</div>}

      <div className="form-card">
        <div className="form-group">
          <label>Term</label>
          <select value={termId} onChange={(e) => { setTermId(e.target.value); setCandidates(null); }}>
            {terms.map(term => (
              <option key={term.id} value={term.id}>{term.name}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="section-header"><h2>1. Courses</h2></div>
      <div className="form-card">
        <div className="schedule-course-picker">
          {availableCourses
            .filter(c => !myCourses.some(mc => mc.id === c.id))
            .map(course => (
              <button
                type="button"
                key={course.id}
                className="btn-sm"
                onClick={() => handleAddCourse(course)}
              >
                + {course.title}
              </button>
            ))}
        </div>

        {myCourses.length === 0 ? (
          <p className="text-muted" style={{ marginTop: 12 }}>No courses added yet.</p>
        ) : (
          <ul className="schedule-my-courses">
            {myCourses.map(course => (
              <li key={course.id}>
                {course.title}
                <button type="button" className="btn-sm btn-danger" onClick={() => handleRemoveCourse(course.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section-header"><h2>2. Breaks</h2></div>
      <div className="form-card">
        <div className="form-row">
          <div className="form-group">
            <label>Day</label>
            <select
              value={newBreak.day_of_week}
              onChange={(e) => setNewBreak({ ...newBreak, day_of_week: e.target.value })}
            >
              {DAY_OPTIONS.map(day => (
                <option key={day.value} value={day.value}>{day.label}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>From</label>
            <input
              type="time"
              value={newBreak.start_time}
              onChange={(e) => setNewBreak({ ...newBreak, start_time: e.target.value })}
            />
          </div>
          <div className="form-group">
            <label>To</label>
            <input
              type="time"
              value={newBreak.end_time}
              onChange={(e) => setNewBreak({ ...newBreak, end_time: e.target.value })}
            />
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label>Label (optional)</label>
            <input
              type="text"
              value={newBreak.label}
              onChange={(e) => setNewBreak({ ...newBreak, label: e.target.value })}
              placeholder="e.g. Work shift"
            />
          </div>
        </div>
        <button type="button" className="btn-secondary" onClick={handleAddBreak}>
          + Add Break
        </button>

        {breaks.length > 0 && (
          <ul className="schedule-my-courses" style={{ marginTop: 14 }}>
            {breaks.map(brk => (
              <li key={brk.id}>
                {DAY_OPTIONS[brk.day_of_week].label} {brk.start_time.slice(0, 5)}-{brk.end_time.slice(0, 5)}
                {brk.label && ` (${brk.label})`}
                <button type="button" className="btn-sm btn-danger" onClick={() => handleRemoveBreak(brk.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section-header"><h2>3. Generate Schedules</h2></div>
      <button
        type="button"
        className="btn-primary"
        onClick={handleGenerate}
        disabled={generating || myCourses.length === 0}
      >
        {generating ? 'Generating...' : 'Generate Schedules'}
      </button>

      {candidates && candidates.length > 0 && (
        <div className="schedule-candidate-viewer">
          <div className="schedule-candidate-nav">
            <button
              type="button"
              className="btn-sm"
              onClick={() => setCandidateIndex(Math.max(0, candidateIndex - 1))}
              disabled={candidateIndex === 0}
            >
              ← Prev
            </button>
            <span>Schedule {candidateIndex + 1} of {candidates.length}</span>
            <button
              type="button"
              className="btn-sm"
              onClick={() => setCandidateIndex(Math.min(candidates.length - 1, candidateIndex + 1))}
              disabled={candidateIndex === candidates.length - 1}
            >
              Next →
            </button>
          </div>

          <WeeklyScheduleCalendar sections={currentCandidate} courseTitleById={courseTitleById} />

          <div className="form-actions">
            {savedScheduleId ? (
              <button type="button" className="btn-primary" onClick={handleConfirm} disabled={confirming}>
                {confirming ? 'Confirming...' : 'Confirm & Enroll'}
              </button>
            ) : (
              <button type="button" className="btn-primary" onClick={handleSave}>
                Save This Schedule
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
