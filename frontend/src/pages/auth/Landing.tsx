import { Link } from 'react-router-dom';
import type { OnboardingRole } from '../../types/auth';
import './auth.css';

export const ROLE_ENTRIES: { role: OnboardingRole; label: string; description: string; detail: string }[] = [
  { role: 'hr', label: 'HR', description: 'People operations', detail: 'Launch onboarding, prepare records, and give final signoff.' },
  { role: 'it', label: 'IT', description: 'Technology & access', detail: 'Prepare equipment, secure access, and coordinate handoff.' },
  { role: 'manager', label: 'Manager', description: 'Team readiness', detail: 'Plan introductions, set goals, and support the first week.' },
  { role: 'employee', label: 'Employee', description: 'Your first chapter', detail: 'Meet your team, finish learning, and get ready for day one.' },
];

export default function Landing() {
  return <div className="landing-page">
    <header className="landing-header"><strong>LaunchOps<span>.</span></strong><Link to="/login">Existing account →</Link></header>
    <main className="landing-content">
      <span className="landing-eyebrow">A SHARED START</span>
      <h1>Great first days<br />start together<span>.</span></h1>
      <p className="landing-intro">One onboarding journey. Four teams moving it forward.<br />Choose your role to enter your workspace.</p>
      <div className="role-grid">{ROLE_ENTRIES.map(entry => <Link className="role-entry" key={entry.role} to={`/login/${entry.role}`}>
        <span className="role-kicker">{entry.description}</span><h2>{entry.label}</h2><p>{entry.detail}</p><strong>Sign in as {entry.label} →</strong>
      </Link>)}</div>
      <p className="landing-note">Use your assigned account. Your role is verified when you sign in.</p>
      <p className="auth-link">Joining as a new hire? <Link to="/register">Create an employee account</Link></p>
    </main>
  </div>;
}
