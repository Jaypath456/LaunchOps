import React from 'react';
import './Leaderboard.css';

// Shared by the live session views (fed by WS leaderboard.update events)
// and the persistent course leaderboard page (fed by a single REST
// fetch) - one rendering path for both, same principle as
// WeeklyScheduleCalendar/LiveBarChart elsewhere in this app.

// Cosmetic only: a stable emoji/color per user_id (not per rank), so a
// player's row keeps the same identity as rankings reshuffle in real
// time - only its position moves, never its color or avatar, which
// would otherwise read as "a different player" every time someone
// overtakes them.
const AVATAR_EMOJI = [
  '🤔', '🍔', '❤️', '🎅', '⚽', '🐺', '🦶', '❄️', '🐻', '🎾',
  '🚀', '🦊', '🐼', '🔥', '⭐', '🎮', '🐸', '🦄', '🍕', '🎨',
];

const BAR_COLORS = [
  '#f87171', '#ef4444', '#6366f1', '#4f46e5', '#312e81',
  '#818cf8', '#a5b4fc', '#fca5a5', '#7c7ce0', '#4338ca',
];

function avatarFor(userId) {
  return AVATAR_EMOJI[Math.abs(userId) % AVATAR_EMOJI.length];
}

function colorFor(userId) {
  return BAR_COLORS[Math.abs(userId * 7 + 3) % BAR_COLORS.length];
}

// Bars are scaled relative to the top score, between a floor (so a
// trailing player's bar is still clearly visible rather than shrinking
// to a sliver) and a ceiling (so even the top score leaves room for its
// own name/emoji label, which sits right after the bar in normal flex
// flow rather than overlaid on top of it) - matches the subtle-length-
// difference look of a real Mentimeter leaderboard, where bar length is
// a secondary cue and the point value is the primary one.
const MIN_BAR_WIDTH_PCT = 40;
const MAX_BAR_WIDTH_PCT = 78;

export default function Leaderboard({ rankings = [], title = 'Leaderboard' }) {
  const topScore = rankings.length > 0 ? Math.max(1, rankings[0].score) : 1;

  return (
    <div className="leaderboard">
      <h3 className="leaderboard-title">{title}</h3>
      {rankings.length === 0 ? (
        <p className="text-muted">No scores yet.</p>
      ) : (
        <ol className="leaderboard-list">
          {rankings.map(entry => {
            const widthPct = Math.min(
              MAX_BAR_WIDTH_PCT,
              Math.max(MIN_BAR_WIDTH_PCT, Math.round((entry.score / topScore) * 100))
            );
            return (
              <li className="leaderboard-row" key={entry.user_id}>
                <span className="leaderboard-points">{entry.score} p</span>
                <div className="leaderboard-bar-container">
                  <div
                    className="leaderboard-bar-fill"
                    style={{ width: `${widthPct}%`, background: colorFor(entry.user_id) }}
                  />
                  <span className="leaderboard-name">
                    <span className="leaderboard-emoji">{avatarFor(entry.user_id)}</span>
                    {entry.username}
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
