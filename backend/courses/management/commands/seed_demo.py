"""Seeds the database with demo data for local development, manual QA,
and as a fixture generator for the load-testing harness (see loadtests/).

Reuses the same object-construction shape as courses/tests.py::setUp
(instructor + student users, a course, chapters with Slate-JSON content,
and enrollments) so the demo data matches the shapes already exercised by
the test suite.

Safe to run multiple times: every object is created with get_or_create,
so re-running this command reuses existing demo data instead of
duplicating it.

Load-test scale (optional, off by default so the plain demo dataset used
by manual QA/onboarding is unaffected):
    python manage.py seed_demo --students 1000
creates loadtest_student_0000..NNNN (same DEMO_PASSWORD), all enrolled
in the first published demo course, plus a load-test quiz with one
question on that course so loadtests/ has a known quiz_id to submit
answers against.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import Chapter, Course, Enrollment
from quizzes.models import Question, Quiz
from users.models import User

DEMO_PASSWORD = 'password123'

DEMO_INSTRUCTORS = [
    {'username': 'demo_instructor', 'email': 'instructor@example.com'},
]

DEMO_STUDENTS = [
    {'username': 'demo_student', 'email': 'student@example.com'},
    {'username': 'demo_student2', 'email': 'student2@example.com'},
    {'username': 'demo_student3', 'email': 'student3@example.com'},
]

DEMO_COURSES = [
    {
        'title': 'Introduction to Python',
        'description': 'A beginner-friendly tour of Python fundamentals.',
        'is_published': True,
        'chapters': [
            {'title': 'Getting Started', 'visibility': 'public', 'order_index': 0},
            {'title': 'Variables & Data Types', 'visibility': 'public', 'order_index': 1},
            {'title': 'Instructor Notes', 'visibility': 'private', 'order_index': 2},
        ],
    },
    {
        'title': 'Advanced Django',
        'description': 'A deep dive into Django internals and best practices.',
        'is_published': True,
        'chapters': [
            {'title': 'ORM Internals', 'visibility': 'public', 'order_index': 0},
            {'title': 'Middleware & Request Lifecycle', 'visibility': 'public', 'order_index': 1},
        ],
    },
    {
        'title': 'Unpublished Draft Course',
        'description': 'Still being authored - should never appear in the student catalog.',
        'is_published': False,
        'chapters': [],
    },
]


LOAD_TEST_QUIZ_TITLE = 'Load Test Quiz'


def _demo_content(text):
    return [{'type': 'paragraph', 'children': [{'text': text}]}]


class Command(BaseCommand):
    help = (
        'Seeds demo instructors, students, courses, chapters, and '
        'enrollments for local development. Idempotent: safe to re-run. '
        'Pass --students N to additionally seed N load-test students '
        '(loadtest_student_0000..NNNN) enrolled in the first published '
        'course, plus a load-test quiz on it - see loadtests/.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--students',
            type=int,
            default=0,
            help='Number of additional loadtest_student_NNNN accounts to seed (default: 0, i.e. none).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        instructors = [
            self._get_or_create_user(data, role='instructor')
            for data in DEMO_INSTRUCTORS
        ]
        students = [
            self._get_or_create_user(data, role='student')
            for data in DEMO_STUDENTS
        ]

        primary_instructor = instructors[0]
        first_published_course = None

        for course_data in DEMO_COURSES:
            course, created = Course.objects.get_or_create(
                title=course_data['title'],
                instructor=primary_instructor,
                defaults={
                    'description': course_data['description'],
                    'is_published': course_data['is_published'],
                },
            )
            self._report('course', course.title, created)

            if course.is_published and first_published_course is None:
                first_published_course = course

            for chapter_data in course_data['chapters']:
                chapter, created = Chapter.objects.get_or_create(
                    course=course,
                    title=chapter_data['title'],
                    defaults={
                        'visibility': chapter_data['visibility'],
                        'order_index': chapter_data['order_index'],
                        'content': _demo_content(
                            f"Demo content for {chapter_data['title']}."
                        ),
                    },
                )
                self._report('chapter', f'{course.title} / {chapter.title}', created)

            if course.is_published:
                for student in students:
                    _enrollment, created = Enrollment.objects.get_or_create(
                        student=student, course=course
                    )
                    self._report(
                        'enrollment', f'{student.username} -> {course.title}', created
                    )

        num_load_test_students = options['students']
        if num_load_test_students > 0:
            self._seed_load_test_scale(first_published_course, num_load_test_students)

        self.stdout.write(self.style.SUCCESS('\nDemo data seeding complete.'))
        self.stdout.write(f"  Instructor login: {DEMO_INSTRUCTORS[0]['username']} / {DEMO_PASSWORD}")
        self.stdout.write(f"  Student login:    {DEMO_STUDENTS[0]['username']} / {DEMO_PASSWORD}")

    def _seed_load_test_scale(self, course, num_students):
        if course is None:
            self.stdout.write(self.style.WARNING(
                '  No published course available to attach load-test students/quiz to - skipping.'
            ))
            return

        quiz, created = Quiz.objects.get_or_create(
            course=course,
            title=LOAD_TEST_QUIZ_TITLE,
            defaults={'description': 'Seeded for loadtests/ - do not edit.', 'is_published': True},
        )
        self._report('quiz', quiz.title, created)

        question, created = Question.objects.get_or_create(
            quiz=quiz,
            question_type=Question.SINGLE_CHOICE,
            defaults={
                'body': {
                    'prompt': _demo_content('What is 2 + 2?'),
                    'options': [{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}],
                    'correct_option_ids': ['b'],
                },
                'points': 1,
            },
        )
        self._report('question', f'{quiz.title} / {question.id}', created)

        for i in range(num_students):
            username = f'loadtest_student_{i:04d}'
            student, created = User.objects.get_or_create(
                username=username,
                defaults={'email': f'{username}@example.com', 'role': 'student'},
            )
            if created:
                student.set_password(DEMO_PASSWORD)
                student.save(update_fields=['password'])
            Enrollment.objects.get_or_create(student=student, course=course)

        self.stdout.write(
            f'  [loadtest] seeded/verified {num_students} students '
            f'(loadtest_student_0000..{num_students - 1:04d}) enrolled in "{course.title}", '
            f'quiz_id={quiz.id}, question_id={question.id}'
        )

    def _get_or_create_user(self, data, role):
        user, created = User.objects.get_or_create(
            username=data['username'],
            defaults={'email': data['email'], 'role': role},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=['password'])
        self._report('user', f"{user.username} ({role})", created)
        return user

    def _report(self, kind, label, created):
        verb = 'created' if created else 'already exists'
        self.stdout.write(f'  [{kind}] {label}: {verb}')
