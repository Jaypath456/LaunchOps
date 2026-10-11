import io
import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from .models import Course, Chapter, Enrollment
from .views import CATALOG_CACHE_KEY
from schedule.models import Term


class ChapterAccessTests(APITestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='instructor',
            password='password123',
            role='instructor'
        )
        self.student = User.objects.create_user(
            username='student',
            password='password123',
            role='student'
        )
        self.course = Course.objects.create(
            title='Intro to LMS',
            description='A short course',
            instructor=self.instructor,
            is_published=True
        )
        self.public_chapter = Chapter.objects.create(
            course=self.course,
            title='Public chapter',
            visibility='public',
            content=[{'type': 'paragraph', 'children': [{'text': 'Hello'}]}]
        )
        self.private_chapter = Chapter.objects.create(
            course=self.course,
            title='Private chapter',
            visibility='private',
            content=[{'type': 'paragraph', 'children': [{'text': 'Secret'}]}]
        )

    def test_student_must_enroll_to_read_public_chapter(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/chapters/{self.public_chapter.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_enrolled_student_can_read_public_chapter(self):
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/chapters/{self.public_chapter.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], self.public_chapter.title)

    def test_enrolled_student_cannot_read_private_chapter(self):
        Enrollment.objects.create(student=self.student, course=self.course)
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/chapters/{self.private_chapter.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_instructor_can_read_private_chapter(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/chapters/{self.private_chapter.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], self.private_chapter.title)


class CatalogCacheTests(APITestCase):
    """Covers Redis-backed caching of the course_list endpoint, its
    invalidation on writes, and graceful degradation if the cache
    backend is unreachable.

    course_list returns a paginated envelope ({count, next, previous,
    results}), so these assertions read response.data['results'] /
    ['count'] rather than treating response.data as a bare list."""

    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            username='cache_student', password='password123', role='student'
        )
        self.instructor = User.objects.create_user(
            username='cache_instructor', password='password123', role='instructor'
        )
        self.client.force_authenticate(user=self.student)

    def tearDown(self):
        cache.clear()

    def test_second_request_is_served_from_cache(self):
        Course.objects.create(title='Cached Course', instructor=self.instructor, is_published=True)

        first = self.client.get('/api/courses/')
        self.assertEqual(len(first.data['results']), 1)

        # A course created after the first request should NOT appear on
        # a second request if it's truly served from cache.
        Course.objects.create(title='Not Yet Cached', instructor=self.instructor, is_published=True)
        second = self.client.get('/api/courses/')

        self.assertEqual(len(second.data['results']), 1)
        self.assertEqual(second.data, first.data)

    def test_cache_is_invalidated_on_course_create(self):
        self.client.force_authenticate(user=self.instructor)
        self.client.get('/api/courses/')  # warm the cache with 0 courses

        self.client.post('/api/courses/create/', {'title': 'Brand New', 'is_published': True}, format='json')

        self.client.force_authenticate(user=self.student)
        response = self.client.get('/api/courses/')
        self.assertEqual(len(response.data['results']), 1)

    def test_cache_is_invalidated_on_publish_toggle(self):
        course = Course.objects.create(title='Draft', instructor=self.instructor, is_published=False)
        self.client.get('/api/courses/')  # warm the cache with 0 published courses

        self.client.force_authenticate(user=self.instructor)
        self.client.put(f'/api/courses/{course.id}/', {'is_published': True}, format='json')

        self.client.force_authenticate(user=self.student)
        response = self.client.get('/api/courses/')
        self.assertEqual(len(response.data['results']), 1)

    def test_cache_is_invalidated_on_course_delete(self):
        course = Course.objects.create(title='To Delete', instructor=self.instructor, is_published=True)
        self.client.get('/api/courses/')  # warm the cache with 1 course

        self.client.force_authenticate(user=self.instructor)
        self.client.delete(f'/api/courses/{course.id}/')

        self.client.force_authenticate(user=self.student)
        response = self.client.get('/api/courses/')
        self.assertEqual(len(response.data['results']), 0)

    def test_catalog_still_works_if_cache_get_is_unavailable(self):
        Course.objects.create(title='Resilient Course', instructor=self.instructor, is_published=True)

        with patch('django.core.cache.cache.get', side_effect=ConnectionError('redis down')):
            response = self.client.get('/api/courses/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_catalog_still_works_if_cache_set_is_unavailable(self):
        Course.objects.create(title='Resilient Course 2', instructor=self.instructor, is_published=True)

        with patch('django.core.cache.cache.set', side_effect=ConnectionError('redis down')):
            response = self.client.get('/api/courses/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_cache_key_used_matches_expected_constant(self):
        # Sanity check that the view is actually using the documented
        # cache key (so ops/debugging docs referencing it stay accurate).
        # Requests with no page/page_size param collapse onto the plain
        # CATALOG_CACHE_KEY (see course_list's cache_key construction).
        self.client.get('/api/courses/')
        self.assertIsNotNone(cache.get(CATALOG_CACHE_KEY))

    def test_different_pages_are_cached_independently(self):
        for i in range(13):  # > one default page (page_size=12)
            Course.objects.create(title=f'Page Course {i}', instructor=self.instructor, is_published=True)

        first_page = self.client.get('/api/courses/')
        second_page = self.client.get('/api/courses/?page=2')

        self.assertEqual(len(first_page.data['results']), 12)
        self.assertEqual(len(second_page.data['results']), 1)
        first_ids = {c['id'] for c in first_page.data['results']}
        second_ids = {c['id'] for c in second_page.data['results']}
        self.assertTrue(first_ids.isdisjoint(second_ids))

        # Re-fetching each page still returns its own cached page, not
        # the other page's cached response.
        first_again = self.client.get('/api/courses/')
        second_again = self.client.get('/api/courses/?page=2')
        self.assertEqual(first_again.data, first_page.data)
        self.assertEqual(second_again.data, second_page.data)

    @override_settings(LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS=True)
    def test_caching_is_skipped_when_load_test_toggle_disables_it(self):
        Course.objects.create(title='Toggle Course', instructor=self.instructor, is_published=True)

        first = self.client.get('/api/courses/')
        self.assertEqual(len(first.data['results']), 1)
        self.assertIsNone(cache.get(CATALOG_CACHE_KEY))

        # With caching disabled, a course created after the first
        # request DOES show up on the very next request (no stale
        # cached response in the way) - the opposite of
        # test_second_request_is_served_from_cache above.
        Course.objects.create(title='Also Visible', instructor=self.instructor, is_published=True)
        second = self.client.get('/api/courses/')
        self.assertEqual(len(second.data['results']), 2)


class SeedDemoCommandTests(TestCase):
    """Covers the seed_demo management command used by the Docker Compose
    workflow (docker compose exec backend python manage.py seed_demo)."""

    def _run_seed(self):
        call_command('seed_demo', stdout=io.StringIO())

    def test_seed_creates_expected_demo_data(self):
        self._run_seed()

        self.assertTrue(User.objects.filter(username='demo_instructor', role='instructor').exists())
        self.assertTrue(User.objects.filter(username='demo_student', role='student').exists())
        self.assertTrue(Course.objects.filter(title='Introduction to Python', is_published=True).exists())
        self.assertTrue(Course.objects.filter(title='Unpublished Draft Course', is_published=False).exists())

        published_course = Course.objects.get(title='Introduction to Python')
        self.assertTrue(Chapter.objects.filter(course=published_course).exists())
        self.assertTrue(Enrollment.objects.filter(course=published_course).exists())

        # Draft courses are never seeded with enrollments.
        draft_course = Course.objects.get(title='Unpublished Draft Course')
        self.assertFalse(Enrollment.objects.filter(course=draft_course).exists())

    def test_seed_is_idempotent(self):
        self._run_seed()
        first_user_count = User.objects.count()
        first_course_count = Course.objects.count()
        first_chapter_count = Chapter.objects.count()
        first_enrollment_count = Enrollment.objects.count()

        self._run_seed()

        self.assertEqual(User.objects.count(), first_user_count)
        self.assertEqual(Course.objects.count(), first_course_count)
        self.assertEqual(Chapter.objects.count(), first_chapter_count)
        self.assertEqual(Enrollment.objects.count(), first_enrollment_count)

    def test_seeded_instructor_can_log_in_with_documented_password(self):
        self._run_seed()

        user = User.objects.get(username='demo_instructor')
        self.assertTrue(user.check_password('password123'))

    def test_students_flag_is_a_no_op_by_default(self):
        self._run_seed()

        self.assertFalse(User.objects.filter(username__startswith='loadtest_student_').exists())

    def test_students_flag_seeds_load_test_scale_data(self):
        call_command('seed_demo', students=25, stdout=io.StringIO())

        loadtest_students = User.objects.filter(username__startswith='loadtest_student_')
        self.assertEqual(loadtest_students.count(), 25)
        self.assertTrue(User.objects.filter(username='loadtest_student_0000').exists())
        self.assertTrue(User.objects.filter(username='loadtest_student_0024').exists())

        published_course = Course.objects.get(title='Introduction to Python')
        self.assertEqual(
            Enrollment.objects.filter(course=published_course, student__username__startswith='loadtest_student_').count(),
            25,
        )

        from quizzes.models import Quiz
        self.assertTrue(Quiz.objects.filter(course=published_course, title='Load Test Quiz').exists())

    def test_students_flag_is_idempotent(self):
        call_command('seed_demo', students=10, stdout=io.StringIO())
        first_count = User.objects.filter(username__startswith='loadtest_student_').count()

        call_command('seed_demo', students=10, stdout=io.StringIO())
        second_count = User.objects.filter(username__startswith='loadtest_student_').count()

        self.assertEqual(first_count, second_count)
        self.assertEqual(first_count, 10)


class EnrollmentPermissionTests(APITestCase):
    """Covers the manage_enrollment fix: role gating + published-only enroll."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='enroll_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='enroll_student', password='password123', role='student'
        )
        self.published_course = Course.objects.create(
            title='Published Course', instructor=self.instructor, is_published=True
        )
        self.draft_course = Course.objects.create(
            title='Draft Course', instructor=self.instructor, is_published=False
        )

    def test_student_can_enroll_in_published_course(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/courses/{self.published_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.published_course).exists()
        )

    def test_student_cannot_enroll_in_unpublished_course(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/courses/{self.draft_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            Enrollment.objects.filter(student=self.student, course=self.draft_course).exists()
        )

    def test_instructor_cannot_enroll(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.post(f'/api/courses/{self.published_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_repeated_enroll_is_idempotent(self):
        self.client.force_authenticate(user=self.student)

        self.client.post(f'/api/courses/{self.published_course.id}/enroll/')
        response = self.client.post(f'/api/courses/{self.published_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Enrollment.objects.filter(student=self.student, course=self.published_course).count(),
            1,
        )

    def test_student_can_unenroll(self):
        Enrollment.objects.create(student=self.student, course=self.published_course)
        self.client.force_authenticate(user=self.student)

        response = self.client.delete(f'/api/courses/{self.published_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Enrollment.objects.filter(student=self.student, course=self.published_course).exists()
        )

    def test_instructor_cannot_unenroll(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.delete(f'/api/courses/{self.published_course.id}/enroll/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CourseOwnershipTests(APITestCase):
    """Regression coverage for the ownership checks refactored onto
    IsCourseInstructor in course_detail/chapter_create."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='course_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='other_instructor', password='password123', role='instructor'
        )
        self.course = Course.objects.create(
            title='Owned Course', instructor=self.owner, is_published=True
        )

    def test_owner_can_update_course(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            f'/api/courses/{self.course.id}/', {'title': 'Updated Title'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Updated Title')

    def test_non_owner_instructor_cannot_update_course(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.put(
            f'/api/courses/{self.course.id}/', {'title': 'Hacked Title'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.course.refresh_from_db()
        self.assertEqual(self.course.title, 'Owned Course')

    def test_non_owner_instructor_cannot_delete_course(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.delete(f'/api/courses/{self.course.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Course.objects.filter(id=self.course.id).exists())

    def test_non_owner_instructor_cannot_create_chapter(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(
            f'/api/courses/{self.course.id}/chapters/create/',
            {'title': 'Sneaky Chapter'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Chapter.objects.filter(course=self.course).exists())


class ChapterOwnershipTests(APITestCase):
    """Regression coverage for the ownership checks refactored onto
    IsCourseInstructor in chapter_detail."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='chapter_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='other_chapter_instructor', password='password123', role='instructor'
        )
        self.course = Course.objects.create(
            title='Course', instructor=self.owner, is_published=True
        )
        self.chapter = Chapter.objects.create(
            course=self.course, title='Chapter 1', visibility='private'
        )

    def test_non_owner_instructor_cannot_update_chapter(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.put(
            f'/api/chapters/{self.chapter.id}/', {'title': 'Hacked'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.chapter.refresh_from_db()
        self.assertEqual(self.chapter.title, 'Chapter 1')

    def test_non_owner_instructor_cannot_delete_chapter(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.delete(f'/api/chapters/{self.chapter.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Chapter.objects.filter(id=self.chapter.id).exists())


class StudentRosterTests(APITestCase):
    """Covers removal of the phantom phone_number field from the roster."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='roster_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='roster_student',
            password='password123',
            email='roster_student@test.com',
            role='student',
        )
        self.course = Course.objects.create(
            title='Roster Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)

    def test_roster_response_has_no_phone_field(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/students/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertNotIn('phone', response.data[0])
        self.assertEqual(response.data[0]['email'], 'roster_student@test.com')


class CourseCatalogQueryTests(APITestCase):
    """Covers the M4 pagination + N+1 fixes for the public catalog."""

    NUM_COURSES = 50

    def setUp(self):
        # The catalog cache is keyed by page/page_size (see
        # courses/views.py::course_list) and lives in Redis, outside
        # Django's per-test transaction rollback - clear it so a cached
        # response from an earlier test can't leak into these query-count
        # assertions.
        cache.clear()
        self.instructor = User.objects.create_user(
            username='catalog_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='catalog_student', password='password123', role='student'
        )

        self.courses = []
        for i in range(self.NUM_COURSES):
            course = Course.objects.create(
                title=f'Course {i}', instructor=self.instructor, is_published=True
            )
            Chapter.objects.create(course=course, title='Chapter 1', visibility='public')
            self.courses.append(course)

        # Enroll the student in a handful of courses so enrolled_count > 0
        # for at least some rows.
        for course in self.courses[:5]:
            Enrollment.objects.create(student=self.student, course=course)

    def test_catalog_response_is_paginated(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/courses/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('results', response.data)
        self.assertEqual(response.data['count'], self.NUM_COURSES)
        self.assertEqual(len(response.data['results']), 12)  # default page_size
        self.assertIsNotNone(response.data['next'])
        self.assertIsNone(response.data['previous'])

    def test_catalog_query_count_is_constant_regardless_of_course_count(self):
        self.client.force_authenticate(user=self.student)

        # 1 query for the annotated/select_related page of courses,
        # 1 query for the paginator's total .count(). This must not grow
        # with the number of courses (the N+1 the audit originally flagged).
        with self.assertNumQueries(2):
            response = self.client.get('/api/courses/')
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_catalog_counts_match_manual_counts(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/courses/')

        for item in response.data['results']:
            course = Course.objects.get(id=item['id'])
            self.assertEqual(item['chapter_count'], course.chapters.count())
            self.assertEqual(item['enrolled_count'], course.enrollments.count())

    def test_can_load_next_page(self):
        self.client.force_authenticate(user=self.student)

        first_page = self.client.get('/api/courses/')
        next_url = first_page.data['next']
        # DRF returns an absolute URL (http://testserver/api/courses/?page=2);
        # the Django test client only needs the path + querystring.
        next_path = next_url.split('testserver', 1)[-1]

        second_page = self.client.get(next_path)

        self.assertEqual(second_page.status_code, status.HTTP_200_OK)
        self.assertEqual(len(second_page.data['results']), 12)
        first_ids = {c['id'] for c in first_page.data['results']}
        second_ids = {c['id'] for c in second_page.data['results']}
        self.assertTrue(first_ids.isdisjoint(second_ids))

    def test_instructor_courses_endpoint_is_not_paginated(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get('/api/courses/mine/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), self.NUM_COURSES)

    def test_instructor_courses_counts_are_correct(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get('/api/courses/mine/')

        for item in response.data:
            course = Course.objects.get(id=item['id'])
            self.assertEqual(item['chapter_count'], course.chapters.count())
            self.assertEqual(item['enrolled_count'], course.enrollments.count())

    def test_my_courses_nested_course_counts_still_correct(self):
        # my_courses is not annotated (nested via select_related), so this
        # exercises CourseSerializer's fallback .count() path rather than
        # the annotated fast path - both must return the same numbers.
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/my-courses/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 5)
        for item in response.data:
            course = Course.objects.get(id=item['course']['id'])
            self.assertEqual(item['course']['chapter_count'], course.chapters.count())
            self.assertEqual(item['course']['enrolled_count'], course.enrollments.count())

    def test_course_create_response_has_zero_counts(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.post(
            '/api/courses/create/', {'title': 'Brand New Course'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['chapter_count'], 0)
        self.assertEqual(response.data['enrolled_count'], 0)

    def test_course_detail_counts_match_manual_counts(self):
        self.client.force_authenticate(user=self.student)
        course = self.courses[0]

        response = self.client.get(f'/api/courses/{course.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['chapter_count'], course.chapters.count())
        self.assertEqual(response.data['enrolled_count'], course.enrollments.count())


class CourseChatOpenFieldTests(APITestCase):
    """Covers CourseSerializer.chat_open, the REST-visible signal the
    chat frontend (M15) uses to disable the composer proactively, and
    which messaging/consumers.py's write-lock mirrors server-side."""

    def setUp(self):
        cache.clear()
        self.instructor = User.objects.create_user(
            username='chatopen_instructor', password='password123', role='instructor'
        )
        self.client.force_authenticate(user=self.instructor)

    def tearDown(self):
        cache.clear()

    def test_course_with_no_term_is_always_open(self):
        course = Course.objects.create(title='No Term Course', instructor=self.instructor, is_published=True)

        response = self.client.get(f'/api/courses/{course.id}/')

        self.assertTrue(response.data['chat_open'])

    def test_course_with_future_term_end_date_is_open(self):
        term = Term.objects.create(
            name='Future Term',
            start_date=datetime.date.today(),
            end_date=datetime.date.today() + datetime.timedelta(days=30),
        )
        course = Course.objects.create(
            title='Future Term Course', instructor=self.instructor, is_published=True, term=term
        )

        response = self.client.get(f'/api/courses/{course.id}/')

        self.assertTrue(response.data['chat_open'])

    def test_course_with_past_term_end_date_is_closed(self):
        term = Term.objects.create(
            name='Past Term',
            start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        course = Course.objects.create(
            title='Past Term Course', instructor=self.instructor, is_published=True, term=term
        )

        response = self.client.get(f'/api/courses/{course.id}/')

        self.assertFalse(response.data['chat_open'])
