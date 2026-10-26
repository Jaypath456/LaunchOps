import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import { useAuth } from '../../context/AuthContext';
import { isMine, stateLabel, ownerLabel, formatDate } from './taskState';
import type { User } from '../../types/auth';
import type { CompletionResponse, ResolutionResponse, TaskDetailResponse, TaskStep } from '../../types/onboarding';
import { errorResponse } from '../../types/api';
import './workspace.css';

const formatError = (data: unknown): string => !data ? 'Could not save. Try again.' : typeof data === 'string' ? data :
  Object.entries(Object(data) as Record<string, unknown>).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(', ') : value}`).join(' · ');
interface EvidenceProps {
  step: TaskStep;
}

function Evidence({ step }: EvidenceProps): React.JSX.Element {
  const proof = step.evidence?._proof;
  return <><p>{proof ? `${proof.mode}: ${proof.result}` : 'Historical evidence: preserved as recorded; not independently verified.'}</p>
    <dl className="task-evidence">{Object.entries(step.evidence || {}).filter(([key]) => key !== '_proof').map(([key, value]) =>
      <React.Fragment key={key}><dt>{step.form_fields.find(f => f.name === key)?.label || key}</dt><dd>{value === true ? 'Acknowledged' : String(value)}</dd></React.Fragment>)}</dl>
    {proof && <dl className="task-evidence">
      {proof.reference && <><dt>Demo reference</dt><dd>{proof.reference}</dd></>}
      {proof.asset && <><dt>Simulated device</dt><dd>{proof.asset}</dd></>}
      {proof.version && <><dt>Published handbook</dt><dd>{proof.version}</dd></>}
      {proof.profile && <><dt>Profile reviewed</dt><dd>{proof.profile.employee} · {proof.profile.job_title} · {proof.profile.department} · {formatDate(proof.profile.start_date)}</dd></>}
      {proof.assessments && <><dt>Assessment results</dt><dd>{proof.assessments.map(q => `${q.score}/${q.max_score}`).join(', ')}</dd></>}
      {proof.reviewed_tasks && <><dt>Tasks included in signoff</dt><dd>{proof.reviewed_tasks.length}</dd></>}
    </dl>}</>;
}
export default function TaskDetail(): React.JSX.Element {
  const { stepId } = useParams<{ stepId: string }>();
  const auth = useAuth();
  // ProtectedRoute renders this page only for an authenticated user.
  const user = auth.user! as User & { id?: number };
  const [task, setTask] = useState<TaskDetailResponse | null>(null);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [resolutions, setResolutions] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');
  const load = useCallback(async () => {
    try { const result = await api.get<TaskDetailResponse>(`/onboarding/steps/${stepId}/`); setTask(result.data); }
    catch (err) { setTask(null); setError(errorResponse(err)?.status === 404 ? 'This task is unavailable to your account.' : 'Could not load the task.'); }
  }, [stepId]);
  useEffect(() => {
    setTask(null); setValues({}); setError(''); setNotice(''); load();
    const timer = window.setInterval(load, 15000);
    window.addEventListener('focus', load);
    return () => { window.clearInterval(timer); window.removeEventListener('focus', load); };
  }, [load, user?.id]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return; setBusy(true); setError(''); setNotice('');
    try { await api.post<CompletionResponse>(`/onboarding/steps/${stepId}/complete/`, { evidence: values }); setNotice('Task completed. The activity log has been updated.'); await load(); }
    catch (err) { setError(formatError(errorResponse(err)?.data)); }
    finally { setBusy(false); }
  }
  async function resolve(id: number) {
    setBusy(true); setError('');
    try { await api.post<ResolutionResponse>(`/onboarding/issues/${id}/resolve/`, { resolution: resolutions[id] || '' }); await load(); }
    catch (err) { setError(formatError(errorResponse(err)?.data)); }
    finally { setBusy(false); }
  }
  // A step is only rendered from a loaded response containing its workflow.
  // The step guard below also guarantees task exists for the detail sections.
  const step = task?.step, workflow = task?.workflow!;
  const mine = step && isMine(step, workflow, user);
  return <div className="ops-shell task-page">
    <Link className="learning-link" to="/workspace">← Back to workspace</Link>
    {error && <p className="ops-alert error" role="alert">{error}</p>}
    {notice && <p className="ops-alert success" role="status">{notice}</p>}
    {!task && !error && <p className="ops-empty">Loading task…</p>}
    {step && <>
      <header className="ops-hero"><div><span className="eyebrow">WORKFLOW #{workflow.id} / {workflow.employee} / {step.owner_role.toUpperCase()}</span><h1>{step.title}<span className="hero-dot">.</span></h1><p>{step.description}</p></div><span className={`status ${step.state}`}>{stateLabel(step, workflow, user)}</span></header>
      <div className="task-layout"><section className="ops-panel">
        <h2>{step.state === 'completed' ? 'Completed task' : 'Task details'}</h2>
        <p className="task-callout">{step.completion_mode === 'simulation' ? 'Simulation — uses demo records only. No external identity, account, payroll, device or delivery is verified.' : step.completion_mode === 'assessment' ? 'Completion requires lesson acknowledgments and server-graded assessment passes. Reading and identity are not independently verified.' : 'Local record — plans, feedback and acknowledgments are saved here. External actions are self-reported.'} Do not enter passwords, recovery codes, identity numbers or financial details.</p>
        {workflow.date_needs_correction && <p role="alert" className="ops-alert error">The start date needs HR correction. Open the workspace and use Correct start date. No date has been guessed.</p>}
        {step.state !== 'completed' && <p>{step.state === 'ready' ? 'Prerequisites satisfied.' : 'Prerequisites are not yet satisfied.'} {mine ? 'This task is assigned to you.' : `Assigned ${ownerLabel(step.owner_role)} must complete this task from their workspace’s Ready for you list.`}</p>}
        {!!task!.dependencies.length && <ul>{task!.dependencies.map(p => <li key={p.id}><Link to={`/workspace/tasks/${p.id}`}>{p.title}</Link> — {p.completed ? 'Completed' : 'Waiting'}</li>)}</ul>}
        {step.key === 'profile' && <p>Employee: {workflow.employee} · {workflow.job_title} · {workflow.department} · Starts {formatDate(workflow.start_date)}</p>}
        {task!.handbook && <article className="task-callout"><h3>{task!.handbook.version}</h3>{task!.handbook.sections.map(text => <p key={text}>{text}</p>)}</article>}
        {!!task!.review.length && <section><h3>Records to review</h3>{task!.review.map(item => <details key={item.id}><summary>{item.title} — {item.state} ({item.completion_mode})</summary><Evidence step={item} /></details>)}</section>}
        {task!.issues.map(issue => <div className="task-callout" key={issue.id}><strong>{issue.resolved_at ? 'Resolved (HR self-report)' : 'Unresolved blocker'}</strong><p>{issue.detail}</p>{issue.resolved_at ? <p>{issue.resolution}</p> : user.role === 'hr' && <><label>Resolution (no sensitive details)<input maxLength={240} value={resolutions[issue.id] || ''} onChange={e => setResolutions({ ...resolutions, [issue.id]: e.target.value })} /></label><button type="button" disabled={busy} onClick={() => resolve(issue.id)}>Record resolution</button></>}</div>)}
        {step.key === 'training' && mine && <div className="task-callout">{workflow.training_course_id ? <><p>Acknowledge all lessons, then pass every assessment with at least 70% (all three answers on a three-question quiz).</p><Link className="ops-primary task-course-link" to={`/student/courses/${workflow.training_course_id}`}>Open lessons and quizzes ↗</Link>
          <ul>{task!.lessons.map(c => <li key={c.id}><Link to={`/student/chapters/${c.id}`}>{c.title}</Link> — {c.reviewed ? 'Acknowledged' : 'Needs acknowledgment'}</li>)}</ul>
          <ul className="assessment-list">{task!.assessments.map(q => <li key={q.id}><span>{q.title} · {q.score === null ? 'Not attempted' : `${q.score}/${q.max_score} · ${q.passed ? 'Passed' : 'Retry needed'}`}</span><Link to={`/student/quizzes/${q.id}/${q.score === null ? 'take' : 'result'}`}>{q.score === null ? 'Take quiz' : 'View result'}</Link></li>)}</ul></> : <p>No learning track is linked. Ask HR to assign one.</p>}</div>}
        {step.state === 'completed' ? <><p>Completed by {step.completed_by} on {new Date(step.completed_at).toLocaleString()}.</p><Evidence step={step} /></> : mine && <form className="task-form" onSubmit={submit}>
          <fieldset disabled={busy || step.state !== 'ready'}>
          {step.form_fields.map(field => <label key={field.name}>{field.type === 'checkbox' ? <span className="task-check"><input required type="checkbox" checked={!!values[field.name]} onChange={e => setValues({ ...values, [field.name]: e.target.checked })} />{field.label}</span> : <>{field.label}{field.type === 'select' ? <select aria-label={field.label} required value={(values[field.name] || '') as string} onChange={e => setValues({ ...values, [field.name]: e.target.value })}><option value="">Choose an option</option>{field.options.map(option => <option key={option}>{option}</option>)}</select> : <input aria-label={field.label} required type={field.type === 'date' ? 'date' : 'text'} maxLength={240} value={(values[field.name] || '') as string} onChange={e => setValues({ ...values, [field.name]: e.target.value })} />}</>}</label>)}
          <button className="ops-primary">{busy ? 'Saving…' : step.state === 'blocked' ? 'Waiting for prerequisites' : step.completion_mode === 'simulation' ? 'Run simulation and save result →' : 'Save record and complete task →'}</button></fieldset>
        </form>}
      </section><aside className="ops-panel task-aside"><h2>At a glance</h2><p><strong>Assigned to</strong><br />{ownerLabel(step.owner_role)}</p><p><strong>Due</strong><br />{formatDate(step.due_at)}</p><p>Task records and completion events are saved to this workflow. Progress refreshes every 15 seconds and when this window regains focus.</p></aside></div>
    </>}
  </div>;
}
