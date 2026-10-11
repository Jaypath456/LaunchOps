import asyncio
import json
from unittest.mock import patch

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from courses.models import Course, Enrollment
from lms_project.asgi import application
from users.models import User

from . import live_state
from .models import LiveSession, Question, Quiz, Submission
from .views import _generate_unique_room_code
from .views import _submit_lock_key


def choice_body(options, correct_option_ids, prompt_text='What is 2+2?'):
    return {
        'prompt': [{'type': 'paragraph', 'children': [{'text': prompt_text}]}],
        'options': options,
        'correct_option_ids': correct_option_ids,
    }


def short_answer_body(correct_answer, prompt_text='Name the capital of France.'):
    return {
        'prompt': [{'type': 'paragraph', 'children': [{'text': prompt_text}]}],
        'correct_answer': correct_answer,
    }


class QuizOwnershipTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='quiz_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='quiz_other_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='quiz_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Quiz Course', instructor=self.owner, is_published=True
        )

    def test_owner_can_create_quiz(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            f'/api/courses/{self.course.id}/quizzes/create/',
            {'title': 'Midterm', 'description': 'Covers chapters 1-3'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], 'Midterm')
        self.assertFalse(response.data['is_published'])
        self.assertEqual(response.data['question_count'], 0)

    def test_non_owner_instructor_cannot_create_quiz(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(
            f'/api/courses/{self.course.id}/quizzes/create/', {'title': 'Sneaky Quiz'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Quiz.objects.filter(course=self.course).exists())

    def test_student_cannot_create_quiz(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/courses/{self.course.id}/quizzes/create/', {'title': 'Sneaky Quiz'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_update_and_publish_quiz(self):
        quiz = Quiz.objects.create(course=self.course, title='Draft Quiz')
        self.client.force_authenticate(user=self.owner)

        response = self.client.put(
            f'/api/quizzes/{quiz.id}/', {'is_published': True}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_published'])

    def test_non_owner_instructor_cannot_update_quiz(self):
        quiz = Quiz.objects.create(course=self.course, title='Draft Quiz')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.put(
            f'/api/quizzes/{quiz.id}/', {'title': 'Hacked'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        quiz.refresh_from_db()
        self.assertEqual(quiz.title, 'Draft Quiz')

    def test_non_owner_instructor_cannot_delete_quiz(self):
        quiz = Quiz.objects.create(course=self.course, title='Draft Quiz')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.delete(f'/api/quizzes/{quiz.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Quiz.objects.filter(id=quiz.id).exists())

    def test_owner_can_delete_quiz(self):
        quiz = Quiz.objects.create(course=self.course, title='Draft Quiz')
        self.client.force_authenticate(user=self.owner)

        response = self.client.delete(f'/api/quizzes/{quiz.id}/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Quiz.objects.filter(id=quiz.id).exists())


class QuizVisibilityTests(APITestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='visibility_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='visibility_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Visibility Course', instructor=self.instructor, is_published=True
        )
        self.published_quiz = Quiz.objects.create(
            course=self.course, title='Published Quiz', is_published=True
        )
        self.draft_quiz = Quiz.objects.create(
            course=self.course, title='Draft Quiz', is_published=False
        )

    def test_owner_sees_both_published_and_draft_quizzes(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/quizzes/')

        titles = {quiz['title'] for quiz in response.data}
        self.assertEqual(titles, {'Published Quiz', 'Draft Quiz'})

    def test_student_only_sees_published_quizzes(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/quizzes/')

        titles = {quiz['title'] for quiz in response.data}
        self.assertEqual(titles, {'Published Quiz'})

    def test_student_cannot_read_draft_quiz_detail(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/quizzes/{self.draft_quiz.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_can_read_published_quiz_detail(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/quizzes/{self.published_quiz.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], 'Published Quiz')

    def test_owner_can_read_draft_quiz_detail(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/quizzes/{self.draft_quiz.id}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class QuestionOwnershipTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='question_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='question_other_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='question_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Question Course', instructor=self.owner, is_published=True
        )
        self.quiz = Quiz.objects.create(course=self.course, title='Quiz 1')
        self.question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SINGLE_CHOICE,
            body=choice_body(
                [{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}], ['b']
            ),
        )

    def test_owner_can_create_single_choice_question(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/questions/create/',
            {
                'question_type': 'single_choice',
                'body': choice_body(
                    [{'id': 'a', 'text': 'Paris'}, {'id': 'b', 'text': 'Rome'}], ['a']
                ),
                'points': 2,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['points'], 2)
        self.assertEqual(response.data['body']['correct_option_ids'], ['a'])

    def test_non_owner_instructor_cannot_create_question(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/questions/create/',
            {'question_type': 'single_choice', 'body': choice_body(
                [{'id': 'a', 'text': 'x'}], ['a']
            )},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_create_question(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/questions/create/',
            {'question_type': 'single_choice', 'body': choice_body(
                [{'id': 'a', 'text': 'x'}], ['a']
            )},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_owner_instructor_cannot_update_question(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.put(
            f'/api/questions/{self.question.id}/', {'points': 99}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.question.refresh_from_db()
        self.assertEqual(self.question.points, 1)

    def test_non_owner_instructor_cannot_delete_question(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.delete(f'/api/questions/{self.question.id}/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Question.objects.filter(id=self.question.id).exists())

    def test_owner_can_list_questions_for_quiz(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/questions/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.question.id)
        # Instructor-facing list includes correct answers - only reachable
        # by the owning instructor (see test below).
        self.assertIn('correct_option_ids', response.data[0]['body'])

    def test_non_owner_instructor_cannot_list_questions(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/questions/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_list_questions(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/questions/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_read_update_and_delete_question(self):
        self.client.force_authenticate(user=self.owner)

        get_response = self.client.get(f'/api/questions/{self.question.id}/')
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)

        put_response = self.client.put(
            f'/api/questions/{self.question.id}/', {'points': 5}, format='json'
        )
        self.assertEqual(put_response.status_code, status.HTTP_200_OK)
        self.assertEqual(put_response.data['points'], 5)

        delete_response = self.client.delete(f'/api/questions/{self.question.id}/')
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Question.objects.filter(id=self.question.id).exists())


class QuestionBodyValidationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='validation_owner', password='password123', role='instructor'
        )
        self.course = Course.objects.create(
            title='Validation Course', instructor=self.owner, is_published=True
        )
        self.quiz = Quiz.objects.create(course=self.course, title='Quiz 1')
        self.client.force_authenticate(user=self.owner)

    def _create(self, question_type, body):
        return self.client.post(
            f'/api/quizzes/{self.quiz.id}/questions/create/',
            {'question_type': question_type, 'body': body},
            format='json',
        )

    def test_valid_multiple_choice_question(self):
        response = self._create(
            'multiple_choice',
            choice_body(
                [{'id': 'a', 'text': 'Cat'}, {'id': 'b', 'text': 'Dog'}, {'id': 'c', 'text': 'Rock'}],
                ['a', 'b'],
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_valid_short_answer_question(self):
        response = self._create('short_answer', short_answer_body('Paris'))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_single_choice_rejects_multiple_correct_answers(self):
        response = self._create(
            'single_choice',
            choice_body(
                [{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}], ['a', 'b']
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_choice_question_rejects_empty_options(self):
        response = self._create('single_choice', choice_body([], []))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_choice_question_rejects_correct_id_not_in_options(self):
        response = self._create(
            'single_choice', choice_body([{'id': 'a', 'text': '3'}], ['nonexistent'])
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_short_answer_rejects_blank_correct_answer(self):
        response = self._create('short_answer', short_answer_body('   '))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_body_must_be_an_object(self):
        response = self._create('short_answer', 'not-an-object')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class QuizTakeTests(APITestCase):
    """Covers the sanitized student-facing question view (quiz_take)."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='take_instructor', password='password123', role='instructor'
        )
        self.enrolled_student = User.objects.create_user(
            username='take_enrolled_student', password='password123', role='student'
        )
        self.outsider_student = User.objects.create_user(
            username='take_outsider_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Take Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.enrolled_student, course=self.course)

        self.quiz = Quiz.objects.create(course=self.course, title='Quiz', is_published=True)
        self.draft_quiz = Quiz.objects.create(course=self.course, title='Draft Quiz', is_published=False)

        self.choice_question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SINGLE_CHOICE,
            body=choice_body([{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}], ['b']),
            points=2,
        )
        self.short_question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Paris'),
            points=3,
            order_index=1,
        )

    def test_enrolled_student_can_take_published_quiz(self):
        self.client.force_authenticate(user=self.enrolled_student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/take/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['questions']), 2)

    def test_correct_answer_keys_are_never_present_in_take_response(self):
        self.client.force_authenticate(user=self.enrolled_student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/take/')

        for question in response.data['questions']:
            self.assertNotIn('correct_option_ids', question['body'])
            self.assertNotIn('correct_answer', question['body'])
            # Also check nested inside options, just in case.
            for option in question['body'].get('options', []):
                self.assertEqual(set(option.keys()), {'id', 'text'})

    def test_choice_question_options_are_still_visible(self):
        self.client.force_authenticate(user=self.enrolled_student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/take/')

        choice_q = next(q for q in response.data['questions'] if q['id'] == self.choice_question.id)
        self.assertEqual(
            {opt['text'] for opt in choice_q['body']['options']}, {'3', '4'}
        )

    def test_non_enrolled_student_cannot_take_quiz(self):
        self.client.force_authenticate(user=self.outsider_student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/take/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_instructor_cannot_take_quiz(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/take/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_take_unpublished_quiz(self):
        self.client.force_authenticate(user=self.enrolled_student)

        response = self.client.get(f'/api/quizzes/{self.draft_quiz.id}/take/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class QuizSubmitTests(APITestCase):
    """Covers grading correctness and idempotent submission."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='submit_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='submit_student', password='password123', role='student'
        )
        self.outsider_student = User.objects.create_user(
            username='submit_outsider', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Submit Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)

        self.quiz = Quiz.objects.create(course=self.course, title='Quiz', is_published=True)
        self.q1 = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SINGLE_CHOICE,
            body=choice_body([{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}], ['b']),
            points=2,
            order_index=0,
        )
        self.q2 = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Paris'),
            points=3,
            order_index=1,
        )
        self.correct_answers = {
            str(self.q1.id): ['b'],
            str(self.q2.id): 'paris',  # case-insensitive match
        }
        self.wrong_answers = {
            str(self.q1.id): ['a'],
            str(self.q2.id): 'London',
        }
        self.partial_answers = {
            str(self.q1.id): ['b'],   # correct (2 pts)
            str(self.q2.id): 'London',  # wrong (0 pts)
        }

    def _submit(self, answers, user=None):
        self.client.force_authenticate(user=user or self.student)
        return self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/', {'answers': answers}, format='json'
        )

    def test_fully_correct_submission_scores_max_points(self):
        response = self._submit(self.correct_answers)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 5)
        self.assertEqual(response.data['max_score'], 5)

    def test_fully_wrong_submission_scores_zero(self):
        response = self._submit(self.wrong_answers)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 0)
        self.assertEqual(response.data['max_score'], 5)

    def test_partially_correct_submission_scores_partial_points(self):
        response = self._submit(self.partial_answers)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 2)
        self.assertEqual(response.data['max_score'], 5)

    def test_multiple_choice_requires_exact_set_match(self):
        mc_question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.MULTIPLE_CHOICE,
            body=choice_body(
                [{'id': 'a', 'text': 'Cat'}, {'id': 'b', 'text': 'Dog'}, {'id': 'c', 'text': 'Rock'}],
                ['a', 'b'],
            ),
            points=4,
            order_index=2,
        )
        # Missing one of the two correct options - should not count.
        answers = {**self.correct_answers, str(mc_question.id): ['a']}

        response = self._submit(answers)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 5)  # q1+q2 correct, mc wrong
        self.assertEqual(response.data['max_score'], 9)

    def test_missing_answer_counts_as_incorrect_not_an_error(self):
        response = self._submit({str(self.q1.id): ['b']})  # q2 omitted entirely

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 2)
        self.assertEqual(response.data['max_score'], 5)

    def test_double_submit_is_idempotent(self):
        first = self._submit(self.correct_answers)
        second = self._submit(self.wrong_answers)  # even with different answers

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['score'], second.data['score'])
        self.assertEqual(second.data['score'], 5)  # original score preserved
        self.assertEqual(Submission.objects.filter(quiz=self.quiz, student=self.student).count(), 1)

    def test_triple_submit_still_only_one_row(self):
        self._submit(self.correct_answers)
        self._submit(self.correct_answers)
        self._submit(self.correct_answers)

        self.assertEqual(Submission.objects.filter(quiz=self.quiz, student=self.student).count(), 1)

    def test_non_enrolled_student_cannot_submit(self):
        response = self._submit(self.correct_answers, user=self.outsider_student)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Submission.objects.filter(quiz=self.quiz, student=self.outsider_student).exists())

    def test_instructor_cannot_submit(self):
        response = self._submit(self.correct_answers, user=self.instructor)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_submit_unpublished_quiz(self):
        draft_quiz = Quiz.objects.create(course=self.course, title='Draft', is_published=False)
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/quizzes/{draft_quiz.id}/submit/', {'answers': {}}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_answers_must_be_an_object(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/', {'answers': 'not-an-object'}, format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class QuizSubmitRedisLockTests(APITestCase):
    """Covers the Redis SETNX-style fast path layered in front of the
    DB-level idempotency guarantee (see quiz_submit's docstring/comments
    for the three-layer design)."""

    def setUp(self):
        cache.clear()
        self.instructor = User.objects.create_user(
            username='lock_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='lock_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Lock Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)
        self.quiz = Quiz.objects.create(course=self.course, title='Quiz', is_published=True)
        self.question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Paris'),
            points=5,
        )
        self.client.force_authenticate(user=self.student)

    def tearDown(self):
        cache.clear()

    def test_concurrent_in_flight_request_gets_409(self):
        # Simulates a second request arriving while the first is
        # already mid-flight (lock held, no Submission row yet).
        lock_key = _submit_lock_key(self.quiz.id, self.student.id)
        cache.add(lock_key, True, timeout=60)

        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Submission.objects.filter(quiz=self.quiz, student=self.student).exists())

    def test_lock_is_released_after_successful_submit(self):
        self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )

        lock_key = _submit_lock_key(self.quiz.id, self.student.id)
        self.assertIsNone(cache.get(lock_key))

    def test_lock_is_released_even_when_answers_payload_is_invalid(self):
        # A 400 mid-request must still release the lock, or every
        # subsequent attempt for this quiz+student would 409 forever.
        self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/', {'answers': 'not-an-object'}, format='json'
        )

        lock_key = _submit_lock_key(self.quiz.id, self.student.id)
        self.assertIsNone(cache.get(lock_key))

        # And a follow-up legitimate submit succeeds normally.
        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_submission_still_succeeds_if_redis_is_unavailable(self):
        with patch('django.core.cache.cache.add', side_effect=ConnectionError('redis down')):
            response = self.client.post(
                f'/api/quizzes/{self.quiz.id}/submit/',
                {'answers': {str(self.question.id): 'Paris'}},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['score'], 5)
        self.assertTrue(Submission.objects.filter(quiz=self.quiz, student=self.student).exists())

    def test_lock_does_not_block_a_different_student(self):
        other_student = User.objects.create_user(
            username='lock_other_student', password='password123', role='student'
        )
        Enrollment.objects.create(student=other_student, course=self.course)

        lock_key = _submit_lock_key(self.quiz.id, self.student.id)
        cache.add(lock_key, True, timeout=60)

        self.client.force_authenticate(user=other_student)
        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @override_settings(LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS=True)
    def test_lock_is_skipped_entirely_when_load_test_toggle_disables_it(self):
        # With the fast path off, a pre-held lock has no effect - the
        # submission goes straight through the DB-only path (layer 3),
        # which is exactly the "before" configuration the load tests
        # compare against.
        lock_key = _submit_lock_key(self.quiz.id, self.student.id)
        cache.add(lock_key, True, timeout=60)

        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Submission.objects.filter(quiz=self.quiz, student=self.student).count(), 1)

    @override_settings(LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS=True)
    def test_db_constraint_still_prevents_duplicates_with_toggle_disabled(self):
        # The DB unique_together guarantee (layer 3) must hold
        # regardless of the toggle - this is the whole point of it
        # being a three-layer design rather than depending on Redis.
        self.client.force_authenticate(user=self.student)
        first = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )
        second = self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'London'}},
            format='json',
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data['score'], second.data['score'])
        self.assertEqual(Submission.objects.filter(quiz=self.quiz, student=self.student).count(), 1)


class QuizMyResultTests(APITestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='result_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='result_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Result Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)
        self.quiz = Quiz.objects.create(course=self.course, title='Quiz', is_published=True)
        self.question = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Paris'),
            points=5,
        )

    def test_no_result_before_submitting(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/my-result/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_result_available_after_submitting(self):
        self.client.force_authenticate(user=self.student)
        self.client.post(
            f'/api/quizzes/{self.quiz.id}/submit/',
            {'answers': {str(self.question.id): 'Paris'}},
            format='json',
        )

        response = self.client.get(f'/api/quizzes/{self.quiz.id}/my-result/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['score'], 5)
        self.assertEqual(response.data['max_score'], 5)

    def test_another_students_result_is_not_visible(self):
        other_student = User.objects.create_user(
            username='result_other_student', password='password123', role='student'
        )
        Enrollment.objects.create(student=other_student, course=self.course)
        Submission.objects.create(
            quiz=self.quiz, student=other_student, answers={}, score=0, max_score=5
        )

        self.client.force_authenticate(user=self.student)
        response = self.client.get(f'/api/quizzes/{self.quiz.id}/my-result/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class LiveSessionLifecycleTests(APITestCase):
    """Covers the REST room lifecycle: create/detail/start/end. The
    fine-grained per-question state machine is WebSocket-driven and
    lives in a later milestone - not tested here."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username='session_owner', password='password123', role='instructor'
        )
        self.other_instructor = User.objects.create_user(
            username='session_other_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='session_student', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Session Course', instructor=self.owner, is_published=True
        )
        self.quiz = Quiz.objects.create(course=self.course, title='Live Quiz', is_published=True)
        Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body={'prompt': [], 'correct_answer': 'Paris'},
        )

    def tearDown(self):
        cache.clear()

    def test_owner_can_create_session(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/quizzes/{self.quiz.id}/sessions/')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'lobby')
        self.assertEqual(len(response.data['room_code']), 6)
        self.assertTrue(LiveSession.objects.filter(room_code=response.data['room_code']).exists())

    def test_session_create_seeds_redis_state(self):
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/quizzes/{self.quiz.id}/sessions/')

        state = live_state.get_session_state(response.data['room_code'])
        self.assertEqual(state['status'], 'lobby')
        self.assertEqual(state['quiz_id'], self.quiz.id)
        self.assertEqual(state['host_id'], self.owner.id)

    def test_non_owner_instructor_cannot_create_session(self):
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(f'/api/quizzes/{self.quiz.id}/sessions/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(LiveSession.objects.exists())

    def test_student_cannot_create_session(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.post(f'/api/quizzes/{self.quiz.id}/sessions/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_any_authenticated_user_can_view_session_by_room_code(self):
        session = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='ABC123')

        self.client.force_authenticate(user=self.student)
        response = self.client.get(f'/api/sessions/{session.room_code}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['room_code'], 'ABC123')

    def test_nonexistent_room_code_returns_404(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get('/api/sessions/ZZZZZZ/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_host_can_start_session(self):
        session = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='START1')
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/start/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'active')
        # -1: active, but no question revealed yet - the live quiz
        # consumer's question.advance is what reveals question 0.
        self.assertEqual(response.data['current_question_index'], -1)
        self.assertIsNotNone(response.data['started_at'])

        state = live_state.get_session_state(session.room_code)
        self.assertEqual(state['status'], 'active')

    def test_non_host_cannot_start_session(self):
        session = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='START2')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(f'/api/sessions/{session.room_code}/start/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        session.refresh_from_db()
        self.assertEqual(session.status, LiveSession.LOBBY)

    def test_cannot_start_an_already_active_session(self):
        session = LiveSession.objects.create(
            quiz=self.quiz, host=self.owner, room_code='START3', status=LiveSession.ACTIVE
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/start/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_start_an_ended_session(self):
        session = LiveSession.objects.create(
            quiz=self.quiz, host=self.owner, room_code='START4', status=LiveSession.ENDED
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/start/')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_host_can_end_session_and_redis_state_is_cleared(self):
        session = LiveSession.objects.create(
            quiz=self.quiz, host=self.owner, room_code='END001', status=LiveSession.ACTIVE
        )
        live_state.set_session_state(session)
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/end/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ended')
        self.assertIsNotNone(response.data['ended_at'])
        self.assertIsNone(live_state.get_session_state(session.room_code))

    def test_non_host_cannot_end_session(self):
        session = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='END002')
        self.client.force_authenticate(user=self.other_instructor)

        response = self.client.post(f'/api/sessions/{session.room_code}/end/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        session.refresh_from_db()
        self.assertNotEqual(session.status, LiveSession.ENDED)

    def test_ending_an_already_ended_session_is_idempotent(self):
        session = LiveSession.objects.create(
            quiz=self.quiz, host=self.owner, room_code='END003', status=LiveSession.ENDED
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/end/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ended')

    def test_host_can_end_a_session_still_in_lobby(self):
        session = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='END004')
        self.client.force_authenticate(user=self.owner)

        response = self.client.post(f'/api/sessions/{session.room_code}/end/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ended')


class RoomCodeGenerationTests(APITestCase):
    """Unit-level coverage of the collision-retry logic, independent of
    the REST endpoints above."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='roomcode_owner', password='password123', role='instructor'
        )
        self.course = Course.objects.create(title='Room Code Course', instructor=self.owner)
        self.quiz = Quiz.objects.create(course=self.course, title='Quiz')

    def test_generates_a_code_of_the_expected_length(self):
        code = _generate_unique_room_code()
        self.assertEqual(len(code), 6)

    def test_retries_on_collision_and_eventually_succeeds(self):
        existing = LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='DUPE01')

        with patch('quizzes.views.generate_room_code', side_effect=['DUPE01', 'DUPE01', 'FRESH1']):
            code = _generate_unique_room_code()

        self.assertEqual(code, 'FRESH1')
        self.assertNotEqual(code, existing.room_code)

    def test_raises_after_exhausting_all_attempts(self):
        LiveSession.objects.create(quiz=self.quiz, host=self.owner, room_code='STUCK1')

        with patch('quizzes.views.generate_room_code', return_value='STUCK1'):
            with self.assertRaises(RuntimeError):
                _generate_unique_room_code()


class LiveQuizConsumerTests(TransactionTestCase):
    """Covers the live quiz WebSocket consumer end to end. Uses
    TransactionTestCase rather than TestCase - the same
    database_sync_to_async + TestCase-transaction limitation identified
    in M13/M14 (async DB access runs in a thread that doesn't see
    TestCase's wrapping transaction)."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @database_sync_to_async
    def _setup_session(self, status=LiveSession.ACTIVE, current_question_index=-1):
        self.instructor = User.objects.create_user(
            username='live_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='live_student', password='password123', role='student'
        )
        self.outsider = User.objects.create_user(
            username='live_outsider', password='password123', role='student'
        )
        self.course = Course.objects.create(title='Live Course', instructor=self.instructor, is_published=True)
        Enrollment.objects.create(student=self.student, course=self.course)
        self.quiz = Quiz.objects.create(course=self.course, title='Live Quiz', is_published=True)
        self.q1 = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SINGLE_CHOICE,
            body=choice_body([{'id': 'a', 'text': '3'}, {'id': 'b', 'text': '4'}], ['b']),
            points=2,
            order_index=0,
        )
        self.q2 = Question.objects.create(
            quiz=self.quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Paris'),
            points=3,
            order_index=1,
        )
        self.session = LiveSession.objects.create(
            quiz=self.quiz,
            host=self.instructor,
            room_code='LIVE01',
            status=status,
            current_question_index=current_question_index,
        )
        live_state.set_session_state(self.session)

    @database_sync_to_async
    def _make_extra_enrolled_student(self, username):
        user = User.objects.create_user(username=username, password='password123', role='student')
        Enrollment.objects.create(student=user, course=self.course)
        return user

    @database_sync_to_async
    def _access_token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    async def _connect(self, user, room_code=None):
        token = await self._access_token(user)
        code = room_code or self.session.room_code
        communicator = WebsocketCommunicator(application, f'/ws/live/{code}/', subprotocols=[token])
        connected, _ = await communicator.connect()
        return communicator, connected

    @database_sync_to_async
    def _refresh_session(self):
        return LiveSession.objects.get(room_code=self.session.room_code)

    @database_sync_to_async
    def _get_submission(self, student):
        return Submission.objects.filter(quiz=self.quiz, student=student).first()

    @database_sync_to_async
    def _submission_count(self, student):
        return Submission.objects.filter(quiz=self.quiz, student=student).count()

    async def test_host_and_enrolled_student_can_connect(self):
        await self._setup_session()

        instr_comm, instr_connected = await self._connect(self.instructor)
        student_comm, student_connected = await self._connect(self.student)

        self.assertTrue(instr_connected)
        self.assertTrue(student_connected)

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_concurrent_joins_preserve_enrollment_permissions(self):
        await self._setup_session()
        users = [self.instructor, self.student, self.outsider] * 4
        connections = await asyncio.gather(*(self._connect(user) for user in users))
        try:
            self.assertEqual([connected for _, connected in connections], [True, True, False] * 4)
            for communicator, connected in connections:
                if connected:
                    state = json.loads(await communicator.receive_from())
                    self.assertEqual(state['type'], 'session.state')
        finally:
            await asyncio.gather(*(comm.disconnect() for comm, _ in connections))

    async def test_accept_echoes_the_token_subprotocol(self):
        # Regression test: real browsers (Chrome confirmed) fail a
        # WebSocket handshake outright if the server's response omits
        # Sec-WebSocket-Protocol after the client offered one - a bare
        # accept() does exactly that. WebsocketCommunicator doesn't
        # enforce this the way a real browser does, so this has to be
        # checked explicitly via connect()'s returned subprotocol rather
        # than relying on `connected` being True. See
        # lms_project/ws_auth.py::negotiated_subprotocol.
        await self._setup_session()
        token = await self._access_token(self.student)

        communicator = WebsocketCommunicator(
            application, f'/ws/live/{self.session.room_code}/', subprotocols=[token]
        )
        connected, subprotocol = await communicator.connect()

        self.assertTrue(connected)
        self.assertEqual(subprotocol, token)

        await communicator.disconnect()

    async def test_non_enrolled_user_is_rejected(self):
        await self._setup_session()

        _comm, connected = await self._connect(self.outsider)

        self.assertFalse(connected)

    async def test_anonymous_connection_is_rejected(self):
        await self._setup_session()

        communicator = WebsocketCommunicator(application, f'/ws/live/{self.session.room_code}/')
        connected, _ = await communicator.connect()

        self.assertFalse(connected)

    async def test_nonexistent_room_is_rejected(self):
        await self._setup_session()

        _comm, connected = await self._connect(self.instructor, room_code='NOPE99')

        self.assertFalse(connected)

    async def test_fresh_join_receives_lobby_state_with_no_question(self):
        await self._setup_session(status=LiveSession.LOBBY, current_question_index=-1)

        comm, _ = await self._connect(self.instructor)
        state = json.loads(await comm.receive_from())

        self.assertEqual(state['type'], 'session.state')
        self.assertEqual(state['status'], 'lobby')
        self.assertNotIn('question', state)

        await comm.disconnect()

    async def test_host_advance_reveals_first_question_to_everyone(self):
        await self._setup_session()

        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()  # initial session.state
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))

        instr_revealed = json.loads(await instr_comm.receive_from())
        student_revealed = json.loads(await student_comm.receive_from())

        self.assertEqual(instr_revealed['type'], 'question.revealed')
        self.assertEqual(instr_revealed['question']['id'], self.q1.id)
        self.assertNotIn('correct_option_ids', instr_revealed['question']['body'])
        self.assertEqual(student_revealed['question']['id'], self.q1.id)

        db_session = await self._refresh_session()
        self.assertEqual(db_session.current_question_index, 0)

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_question_revealed_carries_timer_and_host_only_answer_key(self):
        await self._setup_session()

        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))

        instr_revealed = json.loads(await instr_comm.receive_from())
        student_revealed = json.loads(await student_comm.receive_from())

        # Every listener (host and student alike) gets the timer and a
        # revealed_at timestamp so a countdown can be rendered/resumed.
        self.assertEqual(instr_revealed['question']['time_limit_seconds'], self.q1.time_limit_seconds)
        self.assertIsNotNone(instr_revealed['revealed_at'])
        self.assertEqual(student_revealed['revealed_at'], instr_revealed['revealed_at'])

        # Only the host's copy of the same broadcast carries the answer
        # key - the student's copy must never see it, in either shape.
        self.assertEqual(instr_revealed['correct_option_ids'], ['b'])
        self.assertNotIn('correct_option_ids', student_revealed)
        self.assertNotIn('correct_option_ids', student_revealed['question'])

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_reconnecting_host_gets_answer_key_in_state_snapshot(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.disconnect()
        await student_comm.disconnect()

        # A fresh connection (simulating a reconnect) should see the
        # answer key without needing another advance.
        late_instr_comm, _ = await self._connect(self.instructor)
        late_student_comm, _ = await self._connect(self.student)

        instr_state = json.loads(await late_instr_comm.receive_from())
        student_state = json.loads(await late_student_comm.receive_from())

        self.assertEqual(instr_state['correct_option_ids'], ['b'])
        self.assertNotIn('correct_option_ids', student_state)

        await late_instr_comm.disconnect()
        await late_student_comm.disconnect()

    async def test_non_host_advance_is_rejected_and_ignored(self):
        await self._setup_session()
        comm, _ = await self._connect(self.student)
        await comm.receive_from()

        await comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        db_session = await self._refresh_session()
        self.assertEqual(db_session.current_question_index, -1)

        await comm.disconnect()

    async def test_student_submit_gets_accepted_and_broadcasts_chart(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await student_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )

        accepted = json.loads(await student_comm.receive_from())
        self.assertEqual(accepted['type'], 'answer.accepted')
        # is_correct is what drives the student's own correct/incorrect
        # result screen - it's a private field on this message only,
        # never broadcast, so it can safely reveal correctness even
        # though the answer key itself never reaches students.
        self.assertTrue(accepted['is_correct'])

        # The chart broadcast goes to everyone in the room, including
        # the host - proving the chart is a room-wide broadcast, not a
        # private acknowledgement.
        host_chart = json.loads(await instr_comm.receive_from())
        student_chart = json.loads(await student_comm.receive_from())
        self.assertEqual(host_chart['type'], 'chart.update')
        self.assertEqual(host_chart['counts'], {'b': 1})
        self.assertEqual(student_chart['counts'], {'b': 1})

        submission = await self._get_submission(self.student)
        self.assertEqual(submission.score, 2)  # q1 is worth 2 points, answered correctly

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_incorrect_submit_reports_is_correct_false(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await student_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['a']})
        )

        accepted = json.loads(await student_comm.receive_from())
        self.assertEqual(accepted['type'], 'answer.accepted')
        self.assertFalse(accepted['is_correct'])

        submission = await self._get_submission(self.student)
        self.assertEqual(submission.score, 0)

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_submitting_to_a_non_open_question_is_rejected(self):
        await self._setup_session()  # current_question_index=-1, nothing revealed yet
        comm, _ = await self._connect(self.student)
        await comm.receive_from()

        await comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        self.assertIsNone(await self._get_submission(self.student))

        await comm.disconnect()

    async def test_duplicate_submit_does_not_double_count_score_or_chart(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        # Fire two "submit" events concurrently, simulating a double
        # click / retried request racing itself.
        await asyncio.gather(
            student_comm.send_to(
                text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
            ),
            student_comm.send_to(
                text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
            ),
        )

        # Both requests get an answer.accepted; only the first also
        # triggers a chart broadcast AND a leaderboard broadcast - both
        # reach the whole room, including the submitter's own connection
        # (no special-cased "local echo" - see broadcast_chart_update/
        # broadcast_leaderboard_update).
        student_messages = [
            json.loads(await student_comm.receive_from()) for _ in range(4)
        ]
        student_types = sorted(m['type'] for m in student_messages)
        self.assertEqual(
            student_types,
            ['answer.accepted', 'answer.accepted', 'chart.update', 'leaderboard.update'],
        )

        chart_updates = [m for m in student_messages if m['type'] == 'chart.update']
        self.assertEqual(chart_updates[0]['counts'], {'b': 1})

        leaderboard_updates = [m for m in student_messages if m['type'] == 'leaderboard.update']
        self.assertEqual(leaderboard_updates[0]['rankings'][0]['score'], 2)

        host_chart_update = json.loads(await instr_comm.receive_from())
        self.assertEqual(host_chart_update['type'], 'chart.update')
        self.assertEqual(host_chart_update['counts'], {'b': 1})
        host_leaderboard_update = json.loads(await instr_comm.receive_from())
        self.assertEqual(host_leaderboard_update['type'], 'leaderboard.update')

        # No second chart.update/leaderboard.update was broadcast for
        # the duplicate.
        self.assertTrue(await student_comm.receive_nothing(timeout=0.3))
        self.assertTrue(await instr_comm.receive_nothing(timeout=0.3))

        self.assertEqual(await self._submission_count(self.student), 1)
        submission = await self._get_submission(self.student)
        self.assertEqual(submission.score, 2)  # counted exactly once, not twice

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_chart_counts_match_postgres_submissions_at_question_close(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        await instr_comm.receive_from()
        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()

        second_student = await self._make_extra_enrolled_student('live_student_2')
        s1_comm, _ = await self._connect(self.student)
        s2_comm, _ = await self._connect(second_student)
        await s1_comm.receive_from()
        await s2_comm.receive_from()

        await s1_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )
        await s1_comm.receive_from()  # accepted
        await instr_comm.receive_from()  # chart broadcast
        await instr_comm.receive_from()  # leaderboard broadcast
        await s1_comm.receive_from()  # chart (own)
        await s1_comm.receive_from()  # leaderboard (own)

        await s2_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['a']})
        )
        await s2_comm.receive_from()  # accepted
        final_chart = json.loads(await instr_comm.receive_from())  # chart broadcast
        await instr_comm.receive_from()  # leaderboard broadcast
        await s2_comm.receive_from()  # chart (own)
        await s2_comm.receive_from()  # leaderboard (own)

        # "Question close" here = right after both students have
        # answered; verify the chart's totals equal the number of
        # Postgres Submission rows carrying an answer for this question.
        self.assertEqual(final_chart['counts'], {'b': 1, 'a': 1})
        submissions_with_answer = await self._count_submissions_with_answer(self.q1.id)
        self.assertEqual(sum(final_chart['counts'].values()), submissions_with_answer)

        await instr_comm.disconnect()
        await s1_comm.disconnect()
        await s2_comm.disconnect()

    @database_sync_to_async
    def _count_submissions_with_answer(self, question_id):
        count = 0
        for submission in Submission.objects.filter(quiz=self.quiz):
            if str(question_id) in submission.answers:
                count += 1
        return count

    async def test_two_rooms_do_not_leak_events(self):
        await self._setup_session()

        second_instructor = await self._make_second_instructor_with_quiz()
        second_room = self.second_session.room_code

        room_a_comm, _ = await self._connect(self.instructor)
        room_b_comm, _ = await self._connect(second_instructor, room_code=second_room)
        await room_a_comm.receive_from()
        await room_b_comm.receive_from()

        await room_a_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await room_a_comm.receive_from()

        # Room B must not have received anything from room A's advance.
        self.assertTrue(await room_b_comm.receive_nothing(timeout=0.3))

        await room_a_comm.disconnect()
        await room_b_comm.disconnect()

    @database_sync_to_async
    def _make_second_instructor_with_quiz(self):
        instructor = User.objects.create_user(
            username='live_instructor_2', password='password123', role='instructor'
        )
        course = Course.objects.create(title='Live Course 2', instructor=instructor, is_published=True)
        quiz = Quiz.objects.create(course=course, title='Live Quiz 2', is_published=True)
        Question.objects.create(
            quiz=quiz,
            question_type=Question.SHORT_ANSWER,
            body=short_answer_body('Berlin'),
            points=1,
        )
        self.second_session = LiveSession.objects.create(
            quiz=quiz, host=instructor, room_code='LIVE02', status=LiveSession.ACTIVE
        )
        live_state.set_session_state(self.second_session)
        return instructor

    async def test_non_host_cannot_end_session(self):
        await self._setup_session()
        comm, _ = await self._connect(self.student)
        await comm.receive_from()

        await comm.send_to(text_data=json.dumps({'type': 'session.end'}))
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        db_session = await self._refresh_session()
        self.assertNotEqual(db_session.status, LiveSession.ENDED)

        await comm.disconnect()

    async def test_host_end_broadcasts_and_persists_final_state_and_clears_redis(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'session.end'}))

        instr_ended = json.loads(await instr_comm.receive_from())
        student_ended = json.loads(await student_comm.receive_from())
        self.assertEqual(instr_ended['type'], 'session.ended')
        self.assertEqual(student_ended['type'], 'session.ended')

        db_session = await self._refresh_session()
        self.assertEqual(db_session.status, LiveSession.ENDED)
        self.assertIsNotNone(db_session.ended_at)
        self.assertIsNone(live_state.get_session_state(self.session.room_code))

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_correct_answer_updates_and_broadcasts_leaderboard(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await student_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )
        await student_comm.receive_from()  # answer.accepted
        await instr_comm.receive_from()  # chart.update
        await student_comm.receive_from()  # chart.update

        instr_leaderboard = json.loads(await instr_comm.receive_from())
        student_leaderboard = json.loads(await student_comm.receive_from())

        self.assertEqual(instr_leaderboard['type'], 'leaderboard.update')
        self.assertEqual(len(instr_leaderboard['rankings']), 1)
        self.assertEqual(instr_leaderboard['rankings'][0]['username'], 'live_student')
        self.assertEqual(instr_leaderboard['rankings'][0]['score'], 2)
        self.assertEqual(instr_leaderboard['rankings'][0]['rank'], 1)
        self.assertEqual(student_leaderboard['rankings'], instr_leaderboard['rankings'])

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_wrong_answer_still_appears_on_leaderboard_at_zero(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await student_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['a']})
        )
        await student_comm.receive_from()  # answer.accepted
        await instr_comm.receive_from()  # chart.update
        await student_comm.receive_from()  # chart.update

        leaderboard = json.loads(await instr_comm.receive_from())

        self.assertEqual(len(leaderboard['rankings']), 1)
        self.assertEqual(leaderboard['rankings'][0]['score'], 0)

        await instr_comm.disconnect()
        await student_comm.disconnect()

    async def test_higher_scorer_moves_to_first_place(self):
        # Alice (self.student) answers wrong first (0 pts), Bob answers
        # correctly (2 pts) and should end up ranked above Alice.
        await self._setup_session()
        bob = await self._make_extra_enrolled_student('live_student_bob')

        instr_comm, _ = await self._connect(self.instructor)
        alice_comm, _ = await self._connect(self.student)
        bob_comm, _ = await self._connect(bob)
        await instr_comm.receive_from()
        await alice_comm.receive_from()
        await bob_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await alice_comm.receive_from()
        await bob_comm.receive_from()

        await alice_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['a']})
        )
        await alice_comm.receive_from()
        await instr_comm.receive_from()  # chart
        await alice_comm.receive_from()  # chart (own broadcast)
        await bob_comm.receive_from()  # chart
        first_leaderboard = json.loads(await instr_comm.receive_from())
        await alice_comm.receive_from()  # leaderboard (own broadcast)
        await bob_comm.receive_from()  # leaderboard

        self.assertEqual(first_leaderboard['rankings'][0]['username'], 'live_student')
        self.assertEqual(first_leaderboard['rankings'][0]['score'], 0)

        await bob_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )
        await bob_comm.receive_from()  # accepted
        await instr_comm.receive_from()  # chart
        await alice_comm.receive_from()  # chart
        await bob_comm.receive_from()  # chart (own)
        final_leaderboard = json.loads(await instr_comm.receive_from())

        self.assertEqual(final_leaderboard['rankings'][0]['username'], 'live_student_bob')
        self.assertEqual(final_leaderboard['rankings'][0]['score'], 2)
        self.assertEqual(final_leaderboard['rankings'][0]['rank'], 1)

        await instr_comm.disconnect()
        await alice_comm.disconnect()
        await bob_comm.disconnect()

    async def test_late_joiner_sees_existing_leaderboard(self):
        await self._setup_session()
        instr_comm, _ = await self._connect(self.instructor)
        student_comm, _ = await self._connect(self.student)
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.send_to(text_data=json.dumps({'type': 'question.advance'}))
        await instr_comm.receive_from()
        await student_comm.receive_from()

        await student_comm.send_to(
            text_data=json.dumps({'type': 'answer.submit', 'question_id': self.q1.id, 'answer': ['b']})
        )
        await student_comm.receive_from()
        await instr_comm.receive_from()
        await student_comm.receive_from()
        await instr_comm.receive_from()  # leaderboard broadcast to host
        await student_comm.receive_from()  # leaderboard broadcast to student

        late_student = await self._make_extra_enrolled_student('live_student_late')
        late_comm, connected = await self._connect(late_student)
        state = json.loads(await late_comm.receive_from())

        self.assertTrue(connected)
        self.assertIn('leaderboard', state)
        self.assertEqual(state['leaderboard'][0]['username'], 'live_student')
        self.assertEqual(state['leaderboard'][0]['score'], 2)

        await instr_comm.disconnect()
        await student_comm.disconnect()
        await late_comm.disconnect()

    async def test_leaderboard_is_cleared_on_session_end(self):
        await self._setup_session()
        live_state.increment_leaderboard_score(self.session.room_code, self.student.id, 5)
        instr_comm, _ = await self._connect(self.instructor)
        await instr_comm.receive_from()

        await instr_comm.send_to(text_data=json.dumps({'type': 'session.end'}))
        await instr_comm.receive_from()

        self.assertEqual(live_state.get_leaderboard(self.session.room_code), [])

        await instr_comm.disconnect()


class LiveStateLeaderboardUnitTests(TransactionTestCase):
    """Direct unit coverage of the Redis sorted-set helpers, independent
    of the WebSocket layer above."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_increment_and_get_leaderboard_orders_highest_first(self):
        live_state.increment_leaderboard_score('ROOMX1', 1, 5)
        live_state.increment_leaderboard_score('ROOMX1', 2, 9)
        live_state.increment_leaderboard_score('ROOMX1', 3, 2)

        result = live_state.get_leaderboard('ROOMX1')

        self.assertEqual(result, [('2', 9), ('1', 5), ('3', 2)])

    def test_increment_is_cumulative_across_multiple_calls(self):
        live_state.increment_leaderboard_score('ROOMX2', 1, 3)
        live_state.increment_leaderboard_score('ROOMX2', 1, 4)

        result = live_state.get_leaderboard('ROOMX2')

        self.assertEqual(result, [('1', 7)])

    def test_get_leaderboard_respects_top_n(self):
        for user_id in range(15):
            live_state.increment_leaderboard_score('ROOMX3', user_id, user_id)

        result = live_state.get_leaderboard('ROOMX3', top_n=5)

        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], ('14', 14))

    def test_clear_leaderboard_removes_all_entries(self):
        live_state.increment_leaderboard_score('ROOMX4', 1, 5)
        live_state.clear_leaderboard('ROOMX4')

        self.assertEqual(live_state.get_leaderboard('ROOMX4'), [])

    def test_get_leaderboard_on_empty_room_returns_empty_list(self):
        self.assertEqual(live_state.get_leaderboard('ROOMEMPTY'), [])


class CourseLeaderboardAPITests(APITestCase):
    """Covers GET /api/courses/<id>/leaderboard/ - the persistent,
    course-wide counterpart to the live per-session leaderboard."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='clb_instructor', password='password123', role='instructor'
        )
        self.alice = User.objects.create_user(username='clb_alice', password='password123', role='student')
        self.bob = User.objects.create_user(username='clb_bob', password='password123', role='student')
        self.outsider = User.objects.create_user(
            username='clb_outsider', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Leaderboard Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.alice, course=self.course)
        Enrollment.objects.create(student=self.bob, course=self.course)

        self.quiz_a = Quiz.objects.create(course=self.course, title='Quiz A', is_published=True)
        self.quiz_b = Quiz.objects.create(course=self.course, title='Quiz B', is_published=True)

        Submission.objects.create(quiz=self.quiz_a, student=self.alice, score=5, max_score=10)
        Submission.objects.create(quiz=self.quiz_b, student=self.alice, score=3, max_score=10)
        Submission.objects.create(quiz=self.quiz_a, student=self.bob, score=9, max_score=10)

    def test_enrolled_student_can_view_leaderboard(self):
        self.client.force_authenticate(user=self.alice)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames_in_order = [row['username'] for row in response.data]
        self.assertEqual(usernames_in_order, ['clb_bob', 'clb_alice'])

    def test_scores_are_summed_across_multiple_quizzes(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        alice_row = next(row for row in response.data if row['username'] == 'clb_alice')
        self.assertEqual(alice_row['score'], 8)  # 5 + 3 across quiz_a and quiz_b

    def test_instructor_can_view_leaderboard(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_outsider_cannot_view_leaderboard(self):
        self.client.force_authenticate(user=self.outsider)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ranks_are_assigned_in_order(self):
        self.client.force_authenticate(user=self.alice)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        self.assertEqual(response.data[0]['rank'], 1)
        self.assertEqual(response.data[1]['rank'], 2)

    def test_student_with_no_submissions_does_not_appear(self):
        third_student = User.objects.create_user(
            username='clb_no_submissions', password='password123', role='student'
        )
        Enrollment.objects.create(student=third_student, course=self.course)
        self.client.force_authenticate(user=self.alice)

        response = self.client.get(f'/api/courses/{self.course.id}/leaderboard/')

        usernames = [row['username'] for row in response.data]
        self.assertNotIn('clb_no_submissions', usernames)
