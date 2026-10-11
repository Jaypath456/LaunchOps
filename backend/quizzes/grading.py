"""Server-side quiz grading. The correct answers in Question.body never
leave this module in response to a student-facing request - see
serializers.StudentQuestionSerializer for the sanitized view used by
quiz_take, and views.quiz_submit for how this is invoked.
"""
from .models import Question


def is_answer_correct(question, student_answer):
    body = question.body or {}

    if question.question_type in (Question.SINGLE_CHOICE, Question.MULTIPLE_CHOICE):
        if not isinstance(student_answer, list) or any(not isinstance(value, str) for value in student_answer):
            return False
        correct_ids = set(body.get('correct_option_ids', []))
        given_ids = set(student_answer)
        return given_ids == correct_ids

    if question.question_type == Question.SHORT_ANSWER:
        if not isinstance(student_answer, str):
            return False
        correct_answer = body.get('correct_answer', '')
        return student_answer.strip().lower() == correct_answer.strip().lower()

    return False


def grade_quiz(quiz, answers):
    """Grades every question in `quiz` against `answers`
    ({"<question_id>": <student_answer>}).

    Returns (score, max_score) as plain integers. Unanswered or malformed
    answers are simply marked incorrect (0 points) rather than erroring -
    a student who skips a question should get a valid, if lower, score.
    """
    if not isinstance(answers, dict):
        answers = {}

    score = 0
    max_score = 0

    for question in quiz.questions.all():
        max_score += question.points
        student_answer = answers.get(str(question.id))
        if is_answer_correct(question, student_answer):
            score += question.points

    return score, max_score
