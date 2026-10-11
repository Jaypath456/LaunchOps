import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import api from '../../api/axios';

export default function StudentList() {
  const { courseId } = useParams();
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStudents = async () => {
      try {
        const res = await api.get(`/courses/${courseId}/students/`);
        setStudents(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchStudents();
  }, [courseId]);

  return (
    <div className="page-container">
      <Link to={`/instructor/courses/${courseId}`}>← Back to Course</Link>
      <h1>Enrolled Students</h1>
      {loading ? <p>Loading...</p> : (
        <table className="student-table">
          <thead>
            <tr><th>Name</th><th>Email</th></tr>
          </thead>
          <tbody>
            {students.map(s => (
              <tr key={s.id}><td>{s.name}</td><td>{s.email}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}