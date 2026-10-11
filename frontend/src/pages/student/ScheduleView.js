import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../api/axios';
import WeeklyScheduleCalendar from '../../components/WeeklyScheduleCalendar';
import './student.css';

export default function ScheduleView() {
  const [savedSchedules, setSavedSchedules] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/schedule/saved/')
      .then(res => setSavedSchedules(res.data.filter(s => s.confirmed_at)))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page-container"><p>Loading your schedule...</p></div>;

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>My Schedule</h1>
          <p className="text-muted">Your confirmed course schedules, by term</p>
        </div>
        <Link to="/student/schedule/build" className="btn-primary">Build a New Schedule</Link>
      </div>

      {savedSchedules.length === 0 ? (
        <div className="empty-state">
          <p>You haven't confirmed a schedule yet.</p>
          <Link to="/student/schedule/build" className="btn-primary">Build Your Schedule</Link>
        </div>
      ) : (
        savedSchedules.map(saved => (
          <ConfirmedScheduleCard key={saved.id} saved={saved} />
        ))
      )}
    </div>
  );
}

function ConfirmedScheduleCard({ saved }) {
  const courseTitleById = useMemo(() => {
    const map = {};
    saved.section_details.forEach(s => { map[s.course] = s.section_code || `Course ${s.course}`; });
    return map;
  }, [saved]);

  return (
    <div className="form-card">
      <h2 className="section-title" style={{ margin: '0 0 12px' }}>
        Term #{saved.term} · Confirmed {new Date(saved.confirmed_at).toLocaleDateString()}
      </h2>
      <WeeklyScheduleCalendar sections={saved.section_details} courseTitleById={courseTitleById} height={420} />
    </div>
  );
}
