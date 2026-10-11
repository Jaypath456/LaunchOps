import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '../../api/axios';
import { useAuth } from '../../context/AuthContext';
import './workspace.css';
import { isMine, stateLabel, formatDate } from './taskState';

const LABELS = { hr: 'People operations', it: 'IT operations', manager: 'Team manager', employee: 'New hire' };
const fmt = formatDate;

export default function Workspace() {
  const { user } = useAuth();
  const [workflows, setWorkflows] = useState([]);
  const [directory, setDirectory] = useState({ users: [], tracks: [] });
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingDate, setEditingDate] = useState(false);
  const [revisedDate, setRevisedDate] = useState('');
  const [form, setForm] = useState({ employee_id: '', manager_id: '', training_course_id: '', job_title: '', department: '', start_date: '' });

  const refresh = useCallback(async () => {
    try {
      const result = await api.get('/onboarding/workflows/');
      setWorkflows(result.data);
      setSelectedId(current => result.data.some(w => w.id === current) ? current : result.data[0]?.id || null);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not load workflows. Check the backend connection.');
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    refresh();
    if (user.role === 'hr') api.get('/onboarding/directory/').then(res => setDirectory(res.data)).catch(() => {});
    const interval = window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    return () => { window.clearInterval(interval); window.removeEventListener('focus', refresh); };
  }, [refresh, user.role]);

  const selected = useMemo(() => workflows.find(w => w.id === selectedId), [workflows, selectedId]);
  const allSteps = workflows.flatMap(w => w.steps.map(s => ({ ...s, workflow: w })));
  const actionable = allSteps.filter(s => isMine(s, s.workflow, user) && s.state === 'ready');
  const overdue = allSteps.filter(s => s.state === 'ready' && new Date(s.due_at) < new Date());
  const done = allSteps.filter(s => s.state === 'completed').length;

  async function saveDate(event) {
    event.preventDefault(); setBusy('date'); setError(''); setNotice('');
    try {
      await api.patch(`/onboarding/workflows/${selected.id}/`, { start_date: revisedDate });
      await refresh(); setEditingDate(false); setNotice('Start date and task due dates updated.');
    } catch (err) {
      setError(Object.values(err.response?.data || {}).flat().join(' ') || 'Could not change the date.');
    } finally { setBusy(null); }
  }

  async function changeTrack(value) {
    if (!value) return;
    setBusy('track'); setError('');
    try { await api.patch(`/onboarding/workflows/${selected.id}/`, { training_course_id: Number(value) }); await refresh(); setNotice('Learning track assigned.'); }
    catch (err) { setError(Object.values(err.response?.data || {}).flat().join(' ') || 'Could not assign track.'); }
    finally { setBusy(null); }
  }

  async function launch(event) {
    event.preventDefault(); setBusy('launch'); setError(''); setNotice('');
    try {
      const payload = { ...form, training_course_id: form.training_course_id || null };
      const result = await api.post('/onboarding/workflows/', payload);
      await refresh(); setSelectedId(result.data.id); setShowForm(false);
      setNotice(`Onboarding started for ${result.data.employee}.`);
    } catch (err) {
      const detail = err.response?.data;
      setError(typeof detail === 'object' ? Object.values(detail).flat().join(' ') : 'Could not launch onboarding.');
    } finally { setBusy(null); }
  }

  return <div className="ops-shell">
    <header className="ops-hero">
      <div><span className="eyebrow">WORKSPACE / {LABELS[user.role]?.toUpperCase()}</span>
        <h1>Make day one feel effortless<span className="hero-dot">.</span></h1>
        <p>One shared view of people, permissions, preparation and progress.</p>
      </div>
      {user.role === 'hr' && <button className="ops-primary" onClick={() => setShowForm(!showForm)}>{showForm ? 'Close form' : '+ Start onboarding'}</button>}
    </header>

    {error && <div role="alert" className="ops-alert error">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
    {notice && <div role="status" className="ops-alert success">{notice}<button onClick={() => setNotice('')} aria-label="Dismiss notice">×</button></div>}

    {showForm && <form className="launch-form" onSubmit={launch}>
      <div className="section-heading"><div><span className="eyebrow">NEW WORKFLOW</span><h2>Get a new hire ready</h2></div><span className="muted">HR launches · teams deliver</span></div>
      <div className="form-grid">
        <label>New hire<select aria-label="New hire" required value={form.employee_id} onChange={e => setForm({ ...form, employee_id: e.target.value })}><option value="">Select an employee</option>{directory.users.filter(u => u.role === 'employee').map(u => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
        <label>Manager<select aria-label="Manager" required value={form.manager_id} onChange={e => setForm({ ...form, manager_id: e.target.value })}><option value="">Select a manager</option>{directory.users.filter(u => u.role === 'manager').map(u => <option key={u.id} value={u.id}>{u.username}</option>)}</select></label>
        <label>Job title<input required maxLength="160" value={form.job_title} onChange={e => setForm({ ...form, job_title: e.target.value })} placeholder="Software Engineer" /></label>
        <label>Department<input required maxLength="100" value={form.department} onChange={e => setForm({ ...form, department: e.target.value })} placeholder="Engineering" /></label>
        <label>Start date<input required type="date" value={form.start_date} onChange={e => setForm({ ...form, start_date: e.target.value })} /></label>
        <label>Learning track<select aria-label="Learning track" required value={form.training_course_id} onChange={e => setForm({ ...form, training_course_id: e.target.value })}><option value="">Select a published track</option>{directory.tracks.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label>
      </div><button className="ops-primary" disabled={busy === 'launch'}>{busy === 'launch' ? 'Launching…' : 'Launch workflow →'}</button>
    </form>}

    <div className="ops-metrics">
      <div><span>ACTIVE WORKFLOWS</span><strong>{workflows.filter(w => !w.steps.some(s => s.key === 'ready' && s.state === 'completed')).length}</strong><small>Across your view</small></div>
      <div><span>READY FOR YOU</span><strong>{actionable.length}</strong><small>Steps you can take now</small></div>
      <div><span>COMPLETED STEPS</span><strong>{done}</strong><small>Progress made so far</small></div>
      <div><span>PAST DUE</span><strong>{overdue.length}</strong><small>Ready steps requiring attention</small></div>
    </div>

    {!!actionable.length && <section className="ops-panel ready-list" aria-label="Ready for you"><strong>Ready for you</strong>{actionable.map(s => <Link key={s.id} to={`/workspace/tasks/${s.id}`}>{s.workflow.employee}: {s.title} →</Link>)}</section>}
    {loading ? <div className="ops-empty">Loading workspace…</div> : !workflows.length ? <div className="ops-empty"><h2>No workflows yet</h2><p>{user.role === 'hr' ? 'Start the first onboarding using the button above.' : 'Your onboarding activity will appear here once HR starts a workflow.'}</p></div> : <div className="ops-layout">
      <aside className="ops-panel workflow-list"><div className="panel-title"><span className="eyebrow">PEOPLE</span><h2>Onboarding</h2></div>
        {workflows.map(w => { const count = w.steps.filter(s => s.state === 'completed').length;
          return <button key={w.id} className={`person ${selectedId === w.id ? 'selected' : ''}`} onClick={() => { setSelectedId(w.id); setEditingDate(false); }}>
            <span className="avatar">{w.employee.slice(0, 2).toUpperCase()}</span><span className="person-text"><strong>{w.employee}</strong><small>{w.job_title} · {w.department}</small><span className="mini-progress"><i style={{ width: `${count / w.steps.length * 100}%` }} /></span><small>{count} of {w.steps.length} complete</small></span>
          </button>; })}
      </aside>
      {selected && <div className="ops-main">
        <section className="ops-panel summary-panel"><div className="section-heading"><div><span className="eyebrow">WORKFLOW #{selected.id} · {selected.department.toUpperCase()}</span><h2>{selected.employee}'s first chapter</h2><p>{selected.job_title} · Starts {fmt(selected.start_date)} · Manager {selected.manager}</p>{selected.date_needs_correction && <p role="alert">Start date needs HR correction; existing records are preserved.</p>}{user.role === 'hr' && <>{editingDate ? <form className="date-form" onSubmit={saveDate}><label>Correct start date <input required type="date" value={revisedDate} onChange={e => setRevisedDate(e.target.value)} /></label><button className="complete-btn" disabled={busy === 'date'}>Save date</button><button type="button" className="learning-link" onClick={() => setEditingDate(false)}>Cancel</button></form> : <button className="learning-link link-button" onClick={() => { setRevisedDate(selected.start_date); setEditingDate(true); }}>Correct start date</button>}</>}</div>{user.role === 'hr' && !selected.steps.some(s => s.key === 'training' && s.state === 'completed') && <label>Learning track<select aria-label="Assigned learning track" disabled={busy === 'track'} value={selected.training_course_id || ''} onChange={e => changeTrack(e.target.value)}><option value="">Choose a track</option>{directory.tracks.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label>}<span className="completion-ring">{Math.round(selected.steps.filter(s => s.state === 'completed').length / selected.steps.length * 100)}%</span></div></section>
        <section className="ops-panel"><div className="section-heading"><div><span className="eyebrow">DEPENDENCY-AWARE</span><h2>Launch checklist</h2>{selected.steps.some(s => s.key === 'ready' && s.state === 'completed') && <p>Workflow signed off. Any unfinished tasks added after historical signoff are shown for transparency; the original completion is preserved.</p>}</div><span className="muted">{selected.steps.filter(s => s.state === 'completed').length}/{selected.steps.length} complete</span></div>
          <div className="step-list">{selected.steps.map((step, index) => {
            const mine = step.owner_role === user.role && (user.role !== 'manager' || selected.manager === user.username) && (user.role !== 'employee' || selected.employee === user.username);
            return <article key={step.id} className={`step ${step.state}`}><span className="step-icon">{step.state === 'completed' ? '✓' : String(index + 1).padStart(2, '0')}</span><div className="step-body"><div className="step-head"><h3><Link to={`/workspace/tasks/${step.id}`}>{step.title}</Link></h3><span className={`status ${step.state}`}>{stateLabel(step, selected, user)}</span></div><p>{step.description}</p><div className="step-meta"><span>{step.owner_role.toUpperCase()}</span><span>Due {fmt(step.due_at)}</span>{step.prerequisites.length > 0 && <span>After {step.prerequisites.join(', ')}</span>}</div>
              {step.key === 'training' && selected.training_course_id && selected.employee === user.username && <Link className="learning-link" to={`/student/courses/${selected.training_course_id}`}>Open learning track ↗</Link>}
              <Link className="complete-btn task-open" to={`/workspace/tasks/${step.id}`}>{step.state === 'ready' && mine ? 'Open and complete task →' : 'View task details →'}</Link>
              {step.completed_by && <small className="completed-by">Confirmed by {step.completed_by}</small>}
            </div></article>; })}</div>
        </section>
        <section className="ops-panel"><div className="section-heading"><div><span className="eyebrow">ACCOUNTABILITY</span><h2>Activity log</h2></div></div><div className="event-list">{selected.events.map(event => <div key={event.id}><span className="event-marker"/><div><strong>{event.detail}</strong><small>{event.actor || 'System'} · {fmt(event.created_at)} · {event.action}</small></div></div>)}</div></section>
      </div>}
    </div>}
  </div>;
}
