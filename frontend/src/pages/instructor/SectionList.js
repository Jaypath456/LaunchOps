import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import './instructor.css';

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function formatMeetings(meetings) {
  if (!meetings || meetings.length === 0) return 'No meeting times set';
  return meetings
    .map(m => `${DAY_LABELS[m.day_of_week]} ${m.start_time.slice(0, 5)}-${m.end_time.slice(0, 5)}`)
    .join(', ');
}

export default function SectionList() {
  const { courseId } = useParams();
  const [sections, setSections] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchSections = useCallback(async () => {
    try {
      const res = await api.get(`/courses/${courseId}/sections/`);
      setSections(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [courseId]);

  useEffect(() => {
    fetchSections();
  }, [fetchSections]);

  const handleDelete = async (sectionId) => {
    if (!window.confirm('Delete this section and its meeting times?')) return;
    await api.delete(`/sections/${sectionId}/`);
    fetchSections();
  };

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <div className="breadcrumb">
            <Link to={`/instructor/courses/${courseId}`}>Course</Link> / Sections
          </div>
          <h1>Sections &amp; Schedule</h1>
        </div>
        <Link to={`/instructor/courses/${courseId}/sections/create`} className="btn-primary">
          + Add Section
        </Link>
      </div>

      {loading ? (
        <p>Loading sections...</p>
      ) : sections.length === 0 ? (
        <div className="empty-state">
          <p>No sections yet. Add a section to schedule when this course meets.</p>
          <Link to={`/instructor/courses/${courseId}/sections/create`} className="btn-primary">
            Add Section
          </Link>
        </div>
      ) : (
        <div className="chapter-list">
          {sections.map((section) => (
            <div className="chapter-item" key={section.id}>
              <div className="chapter-left">
                <div>
                  <div className="chapter-title">
                    {section.section_code || 'Section'} {section.location && `- ${section.location}`}
                  </div>
                  <div className="chapter-meta text-muted" style={{ fontSize: 13 }}>
                    {formatMeetings(section.meetings)}
                    {section.capacity != null && ` · capacity ${section.capacity}`}
                  </div>
                </div>
              </div>
              <div className="chapter-actions">
                <Link to={`/instructor/sections/${section.id}/edit`} className="btn-sm">Edit</Link>
                <button onClick={() => handleDelete(section.id)} className="btn-sm btn-danger">
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
