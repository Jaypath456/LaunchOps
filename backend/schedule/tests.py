import unittest

from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from users.models import User

from .models import Break, Meeting, SavedSchedule, Section, Term
from .services import generate_schedules, intervals_overlap


def candidate(id, meetings):
    """Builds a course_groups candidate dict for the pure generator -
    meetings is a list of (day, start, end) string tuples."""
    return {'id': id, 'meetings': meetings}


class IntervalsOverlapTests(unittest.TestCase):
    def test_same_day_overlapping_returns_true(self):
        self.assertTrue(intervals_overlap((0, '09:00', '10:00'), (0, '09:30', '10:30')))

    def test_same_day_touching_at_boundary_does_not_overlap(self):
        # 9-10 and 10-11 share an endpoint but don't actually overlap.
        self.assertFalse(intervals_overlap((0, '09:00', '10:00'), (0, '10:00', '11:00')))

    def test_same_day_non_overlapping_returns_false(self):
        self.assertFalse(intervals_overlap((0, '09:00', '10:00'), (0, '11:00', '12:00')))

    def test_different_days_never_overlap_even_with_same_times(self):
        self.assertFalse(intervals_overlap((0, '09:00', '10:00'), (1, '09:00', '10:00')))

    def test_one_interval_fully_containing_another_overlaps(self):
        self.assertTrue(intervals_overlap((0, '08:00', '12:00'), (0, '09:00', '10:00')))


class GenerateSchedulesUnitTests(unittest.TestCase):
    """Pure-function tests - no Django DB involved at all."""

    def test_no_conflict_single_section_per_course_returns_one_combination(self):
        course_groups = [
            [candidate('A1', [(0, '09:00', '09:50')])],
            [candidate('B1', [(1, '10:00', '10:50')])],
        ]

        results = generate_schedules(course_groups)

        self.assertEqual(len(results), 1)
        self.assertEqual([c['id'] for c in results[0]], ['A1', 'B1'])

    def test_all_conflicting_sections_returns_zero_combinations(self):
        course_groups = [
            [candidate('A1', [(0, '09:00', '10:00')])],
            [candidate('B1', [(0, '09:30', '10:30')])],  # overlaps A1
        ]

        results = generate_schedules(course_groups)

        self.assertEqual(results, [])

    def test_blocked_interval_excludes_conflicting_section(self):
        course_groups = [[candidate('A1', [(0, '09:00', '10:00')])]]
        blocked = [(0, '09:30', '09:45')]  # inside A1's meeting

        results = generate_schedules(course_groups, blocked_intervals=blocked)

        self.assertEqual(results, [])

    def test_blocked_interval_on_different_day_does_not_exclude(self):
        course_groups = [[candidate('A1', [(0, '09:00', '10:00')])]]
        blocked = [(1, '09:00', '10:00')]  # different day

        results = generate_schedules(course_groups, blocked_intervals=blocked)

        self.assertEqual(len(results), 1)

    def test_multi_section_course_picks_only_non_conflicting_combinations(self):
        # Course A offers two sections; Course B offers one. A2 conflicts
        # with B1, A1 doesn't - only the A1+B1 combination should survive.
        course_groups = [
            [
                candidate('A1', [(0, '09:00', '09:50')]),
                candidate('A2', [(0, '10:00', '10:50')]),
            ],
            [candidate('B1', [(0, '10:00', '10:50')])],
        ]

        results = generate_schedules(course_groups)

        self.assertEqual(len(results), 1)
        self.assertEqual([c['id'] for c in results[0]], ['A1', 'B1'])

    def test_multiple_valid_combinations_all_returned(self):
        # Neither of A's sections conflicts with B's - both combinations
        # should be valid.
        course_groups = [
            [
                candidate('A1', [(0, '09:00', '09:50')]),
                candidate('A2', [(1, '09:00', '09:50')]),
            ],
            [candidate('B1', [(2, '09:00', '09:50')])],
        ]

        results = generate_schedules(course_groups)

        combos = {tuple(c['id'] for c in combo) for combo in results}
        self.assertEqual(combos, {('A1', 'B1'), ('A2', 'B1')})

    def test_course_with_no_sections_yields_zero_combinations(self):
        course_groups = [
            [candidate('A1', [(0, '09:00', '09:50')])],
            [],  # course B has no sections at all this term
        ]

        results = generate_schedules(course_groups)

        self.assertEqual(results, [])

    def test_empty_course_groups_returns_no_combinations(self):
        self.assertEqual(generate_schedules([]), [])

    def test_a_section_meeting_multiple_times_per_week_is_fully_checked(self):
        # A1 meets Mon and Wed; B1 only conflicts on the Wed slot.
        course_groups = [
            [candidate('A1', [(0, '09:00', '09:50'), (2, '09:00', '09:50')])],
            [candidate('B1', [(2, '09:30', '10:00')])],
        ]

        results = generate_schedules(course_groups)

        self.assertEqual(results, [])

    def test_max_results_caps_output_size(self):
        # 5 courses x 4 non-conflicting sections each = 1024 valid
        # combinations (all on different days/times) - should be capped.
        course_groups = []
        for course_index in range(5):
            group = []
            for section_index in range(4):
                day = (course_index * 4 + section_index) % 7
                group.append(candidate(f'{course_index}-{section_index}', [(day, '09:00', '09:50')]))
            course_groups.append(group)

        results = generate_schedules(course_groups, max_results=50)

        self.assertEqual(len(results), 50)

    def test_max_nodes_bounds_work_even_when_everything_conflicts(self):
        # A pathological case where every candidate in every group after
        # the first conflicts with everything already chosen - forces the
        # backtracker to visit many nodes without ever finding a full
        # combination. max_nodes must still bound the work performed.
        course_groups = [
            [candidate(f'0-{i}', [(0, '09:00', '10:00')]) for i in range(20)],
        ]
        for course_index in range(1, 8):
            group = [candidate(f'{course_index}-{i}', [(0, '09:00', '10:00')]) for i in range(20)]
            course_groups.append(group)

        # Every candidate after the first course conflicts with whatever
        # was chosen for course 0 (all on the same day/time) - this
        # explores many nodes at depth 1 without ever reaching a full
        # combination, but must still terminate promptly.
        results = generate_schedules(course_groups, max_results=200, max_nodes=500)

        self.assertEqual(results, [])


class TermTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='term_user', password='password123', role='student')
        Term.objects.create(name='Summer 2026', start_date='2026-06-01', end_date='2026-08-15')
        Term.objects.create(name='Fall 2026', start_date='2026-09-01', end_date='2026-12-15')

    def test_authenticated_user_can_list_terms(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/terms/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_unauthenticated_user_cannot_list_terms(self):
        response = self.client.get('/api/terms/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SectionOwnershipTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='section_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='section_other_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='section_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Section Course', instructor=self.owner, is_published=True
        )
        self.term = Term.objects.create(
            name='Summer 2026', start_date='2026-06-01', end_date='2026-08-15'
        )

    def _section_payload(self, **overrides):
        payload = {
            'term': self.term.id,
            'section_code': 'LEC 001',
            'location': 'Hoch 114',
            'capacity': 50,
            'meetings': [
                {'day_of_week': 0, 'start_time': '10:00:00', 'end_time': '11:15:00'},
                {'day_of_week': 2, 'start_time': '10:00:00', 'end_time': '11:15:00'},
            ],
        }
        payload.update(overrides)
        return payload

    def test_owner_can_create_section_with_meetings(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/', self._section_payload(), format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['meetings']), 2)
        self.assertEqual(Section.objects.count(), 1)
        self.assertEqual(Meeting.objects.count(), 2)

    def test_non_owner_instructor_cannot_create_section(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/', self._section_payload(), format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Section.objects.exists())

    def test_student_cannot_create_section(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/', self._section_payload(), format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anyone_authenticated_can_read_sections(self):
        section = Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/sections/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], section.id)

    def test_section_list_can_filter_by_term(self):
        other_term = Term.objects.create(
            name='Fall 2026', start_date='2026-09-01', end_date='2026-12-15'
        )
        Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        Section.objects.create(course=self.course, term=other_term, section_code='LEC 002')
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/sections/?term={self.term.id}')

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['section_code'], 'LEC 001')

    def test_non_owner_instructor_cannot_update_section(self):
        section = Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.put(
            f'/api/sections/{section.id}/', {'section_code': 'HACKED'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        section.refresh_from_db()
        self.assertEqual(section.section_code, 'LEC 001')

    def test_owner_can_update_section_meetings(self):
        section = Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        Meeting.objects.create(section=section, day_of_week=0, start_time='09:00:00', end_time='09:50:00')
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            f'/api/sections/{section.id}/',
            {'meetings': [{'day_of_week': 3, 'start_time': '14:00:00', 'end_time': '15:15:00'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(section.meetings.count(), 1)
        self.assertEqual(section.meetings.first().day_of_week, 3)

    def test_non_owner_instructor_cannot_delete_section(self):
        section = Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.delete(f'/api/sections/{section.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Section.objects.filter(id=section.id).exists())

    def test_owner_can_delete_section(self):
        section = Section.objects.create(course=self.course, term=self.term, section_code='LEC 001')
        self.client.force_authenticate(user=self.owner)

        response = self.client.delete(f'/api/sections/{section.id}/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Section.objects.filter(id=section.id).exists())


class MeetingValidationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='meeting_owner', password='password123', role='instructor'
        )
        self.course = Course.objects.create(
            title='Meeting Course', instructor=self.owner, is_published=True
        )
        self.term = Term.objects.create(
            name='Summer 2026', start_date='2026-06-01', end_date='2026-08-15'
        )
        self.client.force_authenticate(user=self.owner)

    def test_rejects_end_time_before_start_time(self):
        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/',
            {
                'term': self.term.id,
                'meetings': [{'day_of_week': 0, 'start_time': '11:00:00', 'end_time': '10:00:00'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_end_time_equal_to_start_time(self):
        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/',
            {
                'term': self.term.id,
                'meetings': [{'day_of_week': 0, 'start_time': '10:00:00', 'end_time': '10:00:00'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_invalid_day_of_week(self):
        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/',
            {
                'term': self.term.id,
                'meetings': [{'day_of_week': 9, 'start_time': '10:00:00', 'end_time': '11:00:00'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accepts_valid_meeting(self):
        response = self.client.post(
            f'/api/courses/{self.course.id}/sections/create/',
            {
                'term': self.term.id,
                'meetings': [{'day_of_week': 4, 'start_time': '09:00:00', 'end_time': '09:50:00'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class BreakTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username='break_student', password='password123', role='student'
        )
        self.other_student = User.objects.create_user(
            username='break_other_student', password='password123', role='student'
        )
        self.instructor = User.objects.create_user(
            username='break_instructor', password='password123', role='instructor'
        )

    def test_student_can_create_and_list_own_breaks(self):
        self.client.force_authenticate(user=self.student)

        create_response = self.client.post(
            '/api/breaks/',
            {'day_of_week': 0, 'start_time': '08:00:00', 'end_time': '10:00:00', 'label': 'No morning classes'},
            format='json',
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        list_response = self.client.get('/api/breaks/')
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]['label'], 'No morning classes')

    def test_instructor_cannot_create_breaks(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.post(
            '/api/breaks/', {'day_of_week': 0, 'start_time': '08:00:00', 'end_time': '10:00:00'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_breaks_rejects_end_before_start(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            '/api/breaks/', {'day_of_week': 0, 'start_time': '10:00:00', 'end_time': '08:00:00'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_only_sees_own_breaks(self):
        Break.objects.create(
            student=self.other_student, day_of_week=1, start_time='09:00:00', end_time='10:00:00'
        )
        Break.objects.create(
            student=self.student, day_of_week=2, start_time='09:00:00', end_time='10:00:00'
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/breaks/')

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['day_of_week'], 2)

    def test_student_can_delete_own_break(self):
        brk = Break.objects.create(
            student=self.student, day_of_week=0, start_time='08:00:00', end_time='10:00:00'
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.delete(f'/api/breaks/{brk.id}/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Break.objects.filter(id=brk.id).exists())

    def test_student_cannot_delete_another_students_break(self):
        brk = Break.objects.create(
            student=self.other_student, day_of_week=0, start_time='08:00:00', end_time='10:00:00'
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.delete(f'/api/breaks/{brk.id}/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Break.objects.filter(id=brk.id).exists())


class CourseTermFieldTests(APITestCase):
    """Covers the nullable Course.term FK added alongside the schedule app."""

    def test_course_can_be_created_without_a_term(self):
        instructor = User.objects.create_user(
            username='term_field_instructor', password='password123', role='instructor'
        )
        course = Course.objects.create(title='Termless Course', instructor=instructor)
        self.assertIsNone(course.term)

    def test_course_term_survives_term_deletion_as_null(self):
        instructor = User.objects.create_user(
            username='term_field_instructor2', password='password123', role='instructor'
        )
        term = Term.objects.create(name='Winter 2026', start_date='2026-01-01', end_date='2026-03-01')
        course = Course.objects.create(title='Termed Course', instructor=instructor, term=term)

        term.delete()
        course.refresh_from_db()

        self.assertIsNone(course.term)


class GenerateScheduleAPITests(APITestCase):
    """API-level tests for the shared generate_schedule endpoint - the
    role-branching (student Breaks vs instructor's own Meetings) lives
    here since the pure function itself (tested above) doesn't know
    about roles at all.
    """

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='gen_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='gen_student', password='password123', role='student'
        )
        self.term = Term.objects.create(
            name='Summer 2026', start_date='2026-06-01', end_date='2026-08-15'
        )
        self.course_a = Course.objects.create(
            title='Course A', instructor=self.instructor, is_published=True
        )
        self.course_b = Course.objects.create(
            title='Course B', instructor=self.instructor, is_published=True
        )

        self.section_a1 = Section.objects.create(course=self.course_a, term=self.term, section_code='A1')
        Meeting.objects.create(section=self.section_a1, day_of_week=0, start_time='09:00:00', end_time='09:50:00')

        self.section_b1 = Section.objects.create(course=self.course_b, term=self.term, section_code='B1')
        Meeting.objects.create(section=self.section_b1, day_of_week=1, start_time='10:00:00', end_time='10:50:00')

    def _generate(self, course_ids, term_id=None, user=None):
        self.client.force_authenticate(user=user or self.student)
        return self.client.post(
            '/api/schedule/generate/',
            {'course_ids': course_ids, 'term_id': term_id or self.term.id},
            format='json',
        )

    def test_requires_term_id(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            '/api/schedule/generate/', {'course_ids': [self.course_a.id]}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_non_empty_course_ids(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            '/api/schedule/generate/', {'course_ids': [], 'term_id': self.term.id}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_gets_non_conflicting_combination(self):
        response = self._generate([self.course_a.id, self.course_b.id])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        section_ids = {s['id'] for s in response.data['schedules'][0]}
        self.assertEqual(section_ids, {self.section_a1.id, self.section_b1.id})

    def test_student_break_excludes_conflicting_schedule(self):
        # Blocks Monday 9-10, which conflicts with section_a1's meeting.
        Break.objects.create(
            student=self.student, day_of_week=0, start_time='08:30:00', end_time='10:00:00'
        )

        response = self._generate([self.course_a.id, self.course_b.id])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_other_students_breaks_do_not_affect_this_student(self):
        other_student = User.objects.create_user(
            username='gen_other_student', password='password123', role='student'
        )
        Break.objects.create(
            student=other_student, day_of_week=0, start_time='08:30:00', end_time='10:00:00'
        )

        response = self._generate([self.course_a.id, self.course_b.id])

        self.assertEqual(response.data['count'], 1)

    def test_instructor_own_other_meeting_excludes_conflicting_schedule(self):
        # The instructor already teaches Course C on Monday 9-10 this
        # term - scheduling A (which also meets then) should be blocked.
        course_c = Course.objects.create(
            title='Course C', instructor=self.instructor, is_published=True
        )
        section_c1 = Section.objects.create(course=course_c, term=self.term, section_code='C1')
        Meeting.objects.create(section=section_c1, day_of_week=0, start_time='09:00:00', end_time='09:50:00')

        response = self._generate([self.course_a.id, self.course_b.id], user=self.instructor)

        self.assertEqual(response.data['count'], 0)

    def test_instructor_editing_own_course_does_not_self_conflict(self):
        # Course A's own section shouldn't count as a conflict against
        # itself just because the instructor teaches it.
        response = self._generate([self.course_a.id, self.course_b.id], user=self.instructor)

        self.assertEqual(response.data['count'], 1)

    def test_course_with_no_sections_this_term_yields_zero_results(self):
        course_d = Course.objects.create(
            title='Course D', instructor=self.instructor, is_published=True
        )
        # No sections created for course_d in this term.

        response = self._generate([self.course_a.id, course_d.id])

        self.assertEqual(response.data['count'], 0)

    def test_schedules_include_full_section_data(self):
        response = self._generate([self.course_a.id, self.course_b.id])

        section_data = response.data['schedules'][0][0]
        self.assertIn('meetings', section_data)
        self.assertIn('section_code', section_data)


class SavedScheduleTests(APITestCase):
    """Covers saving a chosen candidate and confirming it into real
    Enrollments (the same Enrollment table chapters/quizzes/chat already
    gate on - no second membership concept)."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='saved_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='saved_student', password='password123', role='student'
        )
        self.other_student = User.objects.create_user(
            username='saved_other_student', password='password123', role='student'
        )
        self.term = Term.objects.create(
            name='Summer 2026', start_date='2026-06-01', end_date='2026-08-15'
        )
        self.course_a = Course.objects.create(
            title='Saved Course A', instructor=self.instructor, is_published=True
        )
        self.course_b = Course.objects.create(
            title='Saved Course B', instructor=self.instructor, is_published=True
        )
        self.section_a = Section.objects.create(course=self.course_a, term=self.term, section_code='A1')
        self.section_b = Section.objects.create(course=self.course_b, term=self.term, section_code='B1')

    def test_student_can_save_a_candidate(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            '/api/schedule/saved/',
            {'term': self.term.id, 'sections': [self.section_a.id, self.section_b.id]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data['confirmed_at'])
        self.assertEqual(len(response.data['section_details']), 2)

    def test_saving_requires_at_least_one_section(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            '/api/schedule/saved/', {'term': self.term.id, 'sections': []}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_only_sees_own_saved_schedules(self):
        SavedSchedule.objects.create(student=self.other_student, term=self.term)
        saved = SavedSchedule.objects.create(student=self.student, term=self.term)
        saved.sections.set([self.section_a])
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/schedule/saved/')

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], saved.id)

    def test_instructor_cannot_save_a_schedule(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.post(
            '/api/schedule/saved/',
            {'term': self.term.id, 'sections': [self.section_a.id]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_confirm_creates_enrollments_for_every_section_course(self):
        saved = SavedSchedule.objects.create(student=self.student, term=self.term)
        saved.sections.set([self.section_a, self.section_b])
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/schedule/saved/{saved.id}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['confirmed_at'])
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.course_a).exists())
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.course_b).exists())

    def test_confirming_twice_does_not_duplicate_enrollments(self):
        saved = SavedSchedule.objects.create(student=self.student, term=self.term)
        saved.sections.set([self.section_a, self.section_b])
        self.client.force_authenticate(user=self.student)

        self.client.post(f'/api/schedule/saved/{saved.id}/confirm/')
        second_response = self.client.post(f'/api/schedule/saved/{saved.id}/confirm/')

        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Enrollment.objects.filter(student=self.student, course=self.course_a).count(), 1
        )
        self.assertEqual(
            Enrollment.objects.filter(student=self.student, course=self.course_b).count(), 1
        )

    def test_student_cannot_confirm_another_students_saved_schedule(self):
        saved = SavedSchedule.objects.create(student=self.other_student, term=self.term)
        saved.sections.set([self.section_a])
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/schedule/saved/{saved.id}/confirm/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Enrollment.objects.filter(student=self.student, course=self.course_a).exists())
