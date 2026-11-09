import { useEffect, useState } from 'react';
import api from '../../api/axios';
import type { TimelineResponse } from '../../types/onboarding';

export default function Timeline({ workflowId }: { workflowId: string }): React.JSX.Element {
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let request = 0;
    let active = true;
    async function load() {
      const current = ++request;
      try {
        const { data } = await api.get<TimelineResponse>(`/onboarding/workflows/${workflowId}/timeline/`);
        if (!active || current !== request) return;
        setTimeline(data);
        setError(false);
      } catch {
        if (!active || current !== request) return;
        setError(true);
      }
    }
    setTimeline(null);
    setError(false);
    void load();
    const timer = window.setInterval(load, 15000);
    window.addEventListener('focus', load);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', load); };
  }, [workflowId]);

  return <section className="ops-panel" aria-label="Timeline">
    <div className="section-heading"><div><span className="eyebrow">ACCOUNTABILITY</span><h2>Timeline</h2></div></div>
    {error ? <p role="alert">Could not load the timeline. Refresh and try again.</p> : !timeline || timeline.id !== workflowId ?
      <p>Loading timeline…</p> : timeline.events.length === 0 ? <p>No activity yet.</p> :
      <div className="event-list">{timeline.events.map(event => <div key={event.id}>
        <span className="event-marker" /><div><strong>{event.detail || event.action}</strong>
          <small>{event.actor || 'System'} · <time dateTime={event.created_at}>{new Date(event.created_at).toLocaleString()}</time> · {event.action}</small>
        </div>
      </div>)}</div>}
  </section>;
}
