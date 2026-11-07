import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import api from '../../api/axios';
import { useAuth } from '../../context/AuthContext';
import { isMine, stateLabel, ownerLabel, formatDate } from './taskState';
import type { CompletionResponse, ResolutionResponse, TaskDetailResponse, TaskStep } from '../../types/onboarding';
import { errorResponse, mutationError } from '../../types/api';
import './workspace.css';

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
      {proof.version && <><dt>Policy version</dt><dd>{proof.version}</dd></>}
      {proof.profile && <><dt>Profile reviewed</dt><dd>{proof.profile.employee} · {proof.profile.job_title} · {proof.profile.department} · {formatDate(proof.profile.start_date)}</dd></>}
      {proof.reviewed_tasks && <><dt>Tasks included in signoff</dt><dd>{proof.reviewed_tasks.length}</dd></>}
    </dl>}</>;
}
export default function TaskDetail(): React.JSX.Element {
  const { id, key } = useParams<{ id: string; key: string }>();
  const { user } = useAuth();
  return <TaskPage key={`${id}/${key}/${user!.username}/${user!.role}`} workflowId={id!} stepKey={key!} />;
}

function TaskPage({ workflowId, stepKey }: { workflowId: string; stepKey: string }): React.JSX.Element {
  // ProtectedRoute renders this page only for an authenticated user.
  const { user: authenticatedUser } = useAuth();
  const user = authenticatedUser!;
  const [task, setTask] = useState<TaskDetailResponse | null>(null);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [resolutions, setResolutions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const attempt = useRef<{ key: string; payload: string; evidence: Record<string, string | boolean> } | null>(null);
  const requestId = useRef(0);
  const stepUrl = `/onboarding/workflows/${workflowId}/steps/${stepKey}/`;
  const load = useCallback(async () => {
    const request = ++requestId.current;
    try {
      const result = await api.get<TaskDetailResponse>(stepUrl);
      if (request === requestId.current) setTask(result.data);
    } catch (err) {
      if (request !== requestId.current) return;
      setTask(null);
      setError(errorResponse(err)?.status === 404 ? 'This task is unavailable to your account.' : 'Could not load the task.');
    }
  }, [stepUrl]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(load, 15000);
    window.addEventListener('focus', load);
    return () => { requestId.current += 1; window.clearInterval(timer); window.removeEventListener('focus', load); };
  }, [load]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError(''); setNotice('');
    try {
      const payload = JSON.stringify({ evidence: values });
      // Keep the key and the evidence together across uncertain failures. Editing
      // evidence starts a new attempt; a retry of unchanged evidence reuses its key.
      if (!attempt.current || attempt.current.payload !== payload) {
        attempt.current = { key: crypto.randomUUID(), payload, evidence: { ...values } };
      }
      await api.post<CompletionResponse>(`${stepUrl}complete/`, { evidence: attempt.current.evidence }, {
        headers: { 'Idempotency-Key': attempt.current.key },
      });
      setNotice('Task completed. The timeline has been updated.');
      await load();
    } catch (err) { setError(mutationError(err)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function resolve(id: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      await api.post<ResolutionResponse>(`/onboarding/workflows/${workflowId}/issues/${id}/resolve/`, { resolution: resolutions[id] || '' });
      await load();
    } catch (err) { setError(mutationError(err)); }
    finally { inFlight.current = false; setBusy(false); }
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
      <header className="ops-hero"><div><span className="eyebrow">WORKFLOW #{workflow.id} / {workflow.employee} / {step.owner_role.toUpperCase()}</span><h1>{step.title}<span className="hero-dot">.</span></h1><p>{step.description}</p></div><span className={`status ${step.state}`}>{stateLabel(step, workflow, user)}</span>{step.overdue && <span className="status overdue">Overdue</span>}</header>
      <div className="task-layout"><section className="ops-panel">
        <h2>{step.state === 'done' ? 'Completed task' : 'Task details'}</h2>
        <p className="task-callout">{step.completion_mode === 'simulation' ? 'Simulation — uses demo records only. No external identity, account, payroll, device or delivery is verified.' : step.completion_mode === 'attestation' ? 'Acknowledgment recorded; not proof of reading or identity.' : 'Local record — plans, feedback and acknowledgments are saved here. External actions are self-reported.'} Do not enter passwords, recovery codes, identity numbers or financial details.</p>
        {workflow.date_needs_correction && <p role="alert" className="ops-alert error">The start date needs HR correction. Open the workspace and use Correct start date. No date has been guessed.</p>}
        {step.state !== 'done' && <p>{step.state === 'ready' ? 'Prerequisites satisfied.' : 'Prerequisites are not yet satisfied.'} {mine ? 'This task is assigned to you.' : `Assigned ${ownerLabel(step.owner_role)} must complete this task from their workspace’s Ready for you list.`}</p>}
        {!!task!.dependencies.length && <ul>{task!.dependencies.map(p => <li key={p.key}><Link to={`/workspace/workflows/${workflow.id}/tasks/${p.key}`}>{p.title}</Link> — {p.completed ? 'Completed' : 'Waiting'}</li>)}</ul>}
        {step.key === 'profile' && <p>Employee: {workflow.employee} · {workflow.job_title} · {workflow.department} · Starts {formatDate(workflow.start_date)}</p>}
        {task!.handbook && <article className="task-callout"><h3>{task!.handbook.version}</h3>{task!.handbook.sections.map(text => <p key={text}>{text}</p>)}</article>}
        {!!task!.review.length && <section><h3>Records to review</h3>{task!.review.map(item => <details key={item.key}><summary>{item.title} — {item.state} ({item.completion_mode})</summary><Evidence step={item} /></details>)}</section>}
        {task!.issues.map(issue => <div className="task-callout" key={issue.id}><strong>{issue.resolved_at ? 'Resolved (HR self-report)' : 'Unresolved blocker'}</strong><p>{issue.detail}</p>{issue.resolved_at ? <p>{issue.resolution}</p> : user.role === 'hr' && <><label>Resolution (no sensitive details)<input maxLength={240} value={resolutions[issue.id] || ''} onChange={e => setResolutions({ ...resolutions, [issue.id]: e.target.value })} /></label><button type="button" disabled={busy} onClick={() => resolve(issue.id)}>Record resolution</button></>}</div>)}
        {step.state === 'done' ? <><p>Completed by {step.completed_by} on {new Date(step.completed_at).toLocaleString()}.</p><Evidence step={step} /></> : mine && <form className="task-form" onSubmit={submit}>
          <fieldset disabled={busy || step.state !== 'ready'}>
          {step.form_fields.map(field => <label key={field.name}>{field.type === 'checkbox' ? <span className="task-check"><input required type="checkbox" checked={!!values[field.name]} onChange={e => setValues({ ...values, [field.name]: e.target.checked })} />{field.label}</span> : <>{field.label}{field.type === 'select' ? <select aria-label={field.label} required value={(values[field.name] || '') as string} onChange={e => setValues({ ...values, [field.name]: e.target.value })}><option value="">Choose an option</option>{field.options.map(option => <option key={option}>{option}</option>)}</select> : <input aria-label={field.label} required type={field.type === 'date' ? 'date' : 'text'} maxLength={240} value={(values[field.name] || '') as string} onChange={e => setValues({ ...values, [field.name]: e.target.value })} />}</>}</label>)}
          <button className="ops-primary">{busy ? 'Saving…' : step.state === 'blocked' ? 'Waiting for prerequisites' : step.completion_mode === 'simulation' ? 'Run simulation and save result →' : 'Save record and complete task →'}</button></fieldset>
        </form>}
      </section><aside className="ops-panel task-aside"><h2>At a glance</h2><p><strong>Assigned to</strong><br />{ownerLabel(step.owner_role)}</p><p><strong>Due</strong><br />{formatDate(step.due_at)}</p><p>Task records and completion events are saved to this workflow. Progress refreshes every 15 seconds and when this window regains focus.</p></aside></div>
    </>}
  </div>;
}
