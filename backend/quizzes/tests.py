from unittest.mock import patch

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from users.models import User

from .models import Question, Quiz, Submission
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


class CourseLeaderboardAPITests(APITestCase):
    """Covers GET /api/courses/<id>/leaderboard/ - the persistent,
    course-wide total quiz scores."""

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
