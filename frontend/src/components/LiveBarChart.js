import React from 'react';
import './LiveBarChart.css';

// Renders the live per-question counts pushed by chart.update events.
// Mirrors the backend's two bucket schemes (quizzes/consumers.py):
//   single_choice/multiple_choice -> one bucket per option id
//   short_answer                 -> a simple correct/incorrect tally
//
// Instructor-only component (see LiveQuizHost.js): correctOptionIds
// comes from the host-only question.answer_key info merged into that
// consumer's own question.revealed/session.state messages
// (quizzes/consumers.py::_answer_key_for) - never sent to students, so
// this must never be rendered on the student-facing live quiz page.
export default function LiveBarChart({ question, counts = {}, correctOptionIds = [] }) {
  if (!question) return null;

  const isChoice = question.question_type === 'single_choice' || question.question_type === 'multiple_choice';
  const buckets = isChoice
    ? (question.body?.options || []).map(opt => ({ id: opt.id, label: opt.text }))
    : [
        { id: 'correct', label: 'Correct' },
        { id: 'incorrect', label: 'Incorrect' },
      ];

  // For short_answer, the bucket id itself already only ever means
  // "correct"/"incorrect" (see consumers.py::_chart_buckets_for_answer)
  // - the label alone already reveals that, so the tick/cross overlay
  // is always safe to show. For choice questions, only show it once
  // correctOptionIds has actually arrived (host-only payload) - until
  // then, showing crosses on every bar would be misleading, not safer.
  const canShowIndicator = !isChoice || correctOptionIds.length > 0;
  const isCorrectBucket = (bucketId) => (isChoice ? correctOptionIds.includes(bucketId) : bucketId === 'correct');

  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  const maxCount = Math.max(1, ...buckets.map(b => counts[b.id] || 0));

  return (
    <div className="live-bar-chart">
      {buckets.map(bucket => {
        const count = counts[bucket.id] || 0;
        const widthPct = Math.round((count / maxCount) * 100);
        const correct = isCorrectBucket(bucket.id);
        return (
          <div className="live-bar-row" key={bucket.id}>
            <div className="live-bar-label">{bucket.label}</div>
            <div className="live-bar-track">
              <div
                className={`live-bar-fill${canShowIndicator && correct ? ' live-bar-fill-correct' : ''}`}
                style={{ width: `${widthPct}%` }}
              />
              <span className="live-bar-count">
                {count}
                {canShowIndicator && (
                  <span className={`live-bar-indicator ${correct ? 'is-correct' : 'is-incorrect'}`}>
                    {correct ? '✓' : '✗'}
                  </span>
                )}
              </span>
            </div>
          </div>
        );
      })}
      <div className="live-bar-total text-muted">{total} response{total === 1 ? '' : 's'}</div>
    </div>
  );
}
