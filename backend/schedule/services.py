"""Pure, stateless schedule generation.

This module has no Django/ORM dependency at all - it operates entirely on
plain Python data (dicts and tuples), which is what lets it serve both
roles with the exact same code:

* Students: course_groups = the sections on offer for each course they
  want to take; blocked_intervals = their personal Breaks.
* Instructors: course_groups = candidate sections for the course(s) they're
  scheduling; blocked_intervals = their OWN existing Meetings for other
  courses (so they don't double-book themselves).

See schedule/views.py::generate_schedule for how real Section/Meeting/
Break querysets get converted into this shape and back.
"""

Interval = tuple  # (day_of_week: int, start_time, end_time) - comparable values


def intervals_overlap(a, b):
    """True if two (day, start, end) intervals overlap. `start`/`end` just
    need to support < comparison - works with datetime.time or plain
    strings like "09:00:00", which is what keeps this module dependency-
    free (no need to import Django or parse into datetime.time here).
    """
    day_a, start_a, end_a = a
    day_b, start_b, end_b = b
    if day_a != day_b:
        return False
    return start_a < end_b and start_b < end_a


def _has_conflict(candidate_meetings, chosen_meetings, blocked_intervals):
    for meeting in candidate_meetings:
        for other in chosen_meetings:
            if intervals_overlap(meeting, other):
                return True
        for blocked in blocked_intervals:
            if intervals_overlap(meeting, blocked):
                return True
    return False


def generate_schedules(course_groups, blocked_intervals=None, max_results=200, max_nodes=50000):
    """Enumerate every way to pick exactly one candidate from each group
    in `course_groups` such that no two chosen candidates' meetings
    overlap each other or any blocked interval.

    Args:
        course_groups: list of lists of candidates. Each candidate is a
            dict with at least a 'meetings' key: a list of
            (day_of_week, start_time, end_time) tuples. One inner list
            per course - if a course has zero candidates, no schedule
            including that course can ever be valid (this is a
            deliberate, correct outcome, not an error).
        blocked_intervals: list of (day_of_week, start_time, end_time)
            tuples that no chosen meeting may overlap.
        max_results: stop once this many valid combinations have been
            found. Protects API consumers from an unbounded response.
        max_nodes: stop exploring after visiting this many search-tree
            nodes, regardless of how many valid results were found so
            far. Protects against pathological inputs (e.g. many
            courses with many conflict-free sections each) taking
            unbounded time even when every branch is valid.

    Returns:
        A list of combinations. Each combination is a list of one
        candidate per course_group, in the same order as course_groups.
    """
    blocked_intervals = blocked_intervals or []
    results = []
    nodes_visited = 0

    def backtrack(index, chosen, chosen_meetings):
        nonlocal nodes_visited
        if len(results) >= max_results or nodes_visited >= max_nodes:
            return
        nodes_visited += 1

        if index == len(course_groups):
            results.append(list(chosen))
            return

        for candidate in course_groups[index]:
            if len(results) >= max_results or nodes_visited >= max_nodes:
                return
            if _has_conflict(candidate['meetings'], chosen_meetings, blocked_intervals):
                continue
            chosen.append(candidate)
            backtrack(index + 1, chosen, chosen_meetings + list(candidate['meetings']))
            chosen.pop()

    if course_groups:
        backtrack(0, [], [])

    return results
