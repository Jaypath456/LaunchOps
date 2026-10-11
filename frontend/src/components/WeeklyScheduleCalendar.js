import React, { useMemo } from 'react';
import { Calendar, dateFnsLocalizer } from 'react-big-calendar';
import { format, parse, startOfWeek, getDay, addDays } from 'date-fns';
import { enUS } from 'date-fns/locale';
import 'react-big-calendar/lib/css/react-big-calendar.css';

const locales = { 'en-US': enUS };

const localizer = dateFnsLocalizer({
  format,
  parse,
  startOfWeek: () => startOfWeek(new Date(), { weekStartsOn: 1 }),
  getDay,
  locales,
});

function timeStringToDate(baseDate, timeStr) {
  const [hours, minutes] = timeStr.split(':').map(Number);
  const date = new Date(baseDate);
  date.setHours(hours, minutes, 0, 0);
  return date;
}

/**
 * Sections don't carry real dates - they're recurring weekly meetings
 * (day_of_week + time). To render them on react-big-calendar's week
 * view, every meeting is projected onto a single fixed reference week
 * starting this Monday; only the day-of-week and time-of-day are ever
 * meaningful, the actual calendar date shown is not.
 */
export function buildCalendarEvents(sections, courseTitleById = {}) {
  const referenceMonday = startOfWeek(new Date(), { weekStartsOn: 1 });
  const events = [];

  sections.forEach((section) => {
    const title = courseTitleById[section.course] || section.section_code || 'Course';
    (section.meetings || []).forEach((meeting) => {
      const day = addDays(referenceMonday, meeting.day_of_week);
      events.push({
        title: `${title}${section.section_code ? ` (${section.section_code})` : ''}`,
        start: timeStringToDate(day, meeting.start_time),
        end: timeStringToDate(day, meeting.end_time),
        resource: section,
      });
    });
  });

  return events;
}

export default function WeeklyScheduleCalendar({ sections, courseTitleById, height = 520 }) {
  const events = useMemo(
    () => buildCalendarEvents(sections, courseTitleById),
    [sections, courseTitleById]
  );

  return (
    <div style={{ height }}>
      <Calendar
        localizer={localizer}
        events={events}
        defaultView="week"
        views={['week']}
        toolbar={false}
        step={30}
        timeslots={2}
        min={new Date(1970, 0, 1, 7, 0, 0)}
        max={new Date(1970, 0, 1, 21, 0, 0)}
      />
    </div>
  );
}
