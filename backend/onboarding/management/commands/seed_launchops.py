from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from users.models import User
from courses.models import Course, Chapter
from quizzes.models import Quiz, Question
from onboarding.models import Onboarding
from onboarding.services import create_onboarding

PASSWORD = 'LaunchOpsDemo2026!'
LESSONS = [
    ('Your security checklist', 'Use a unique password and MFA. If you see a suspicious sign-in link, report it to IT without opening it.', 'Security readiness check', [
        ('What should you do with a suspicious sign-in link?', 'Open it to check', 'Report it without opening it'),
        ('Which password should you use at work?', 'A reused personal password', 'A unique password'),
        ('What protects a sign-in after the password?', 'Sharing the password', 'Multi-factor authentication')]),
    ('Phishing and reporting', 'Check sender and destination before interacting. Use the designated IT support channel to report suspicious messages; never forward credentials.', 'Phishing response check', [
        ('A sender urgently requests credentials. What next?', 'Send credentials', 'Report to IT'),
        ('A message has a strange attachment. What next?', 'Open it immediately', 'Ask IT to inspect it'),
        ('You clicked a suspicious link. What next?', 'Keep it secret', 'Tell IT promptly')]),
    ('Protecting your device', 'Lock your screen when away, install approved updates, and report a missing device immediately. Do not turn off endpoint protection.', 'Device safety check', [
        ('You leave your workstation. What should you do?', 'Leave it unlocked', 'Lock the screen'),
        ('A device is missing. What should you do?', 'Wait a week', 'Report it to IT immediately'),
        ('An approved security update is ready. What should you do?', 'Disable protection', 'Apply the update')]),
    ('Handling company data', 'Store work files only in approved systems. Share the minimum access necessary, and follow your team retention rules.', 'Data handling check', [
        ('Where should you store work files?', 'A personal cloud account', 'An approved work system'),
        ('How much access should a collaborator receive?', 'Everyone gets full access', 'Only the access they need'),
        ('You sent data to the wrong person. What next?', 'Ignore it', 'Report the incident promptly')]),
    ('Support and incident response', 'Save the IT support channel. When reporting a security issue, describe what happened and when; do not paste passwords or recovery codes into tickets.', 'Incident response check', [
        ('A ticket asks for a recovery code. What should you do?', 'Paste the code', 'Refuse and alert IT'),
        ('What should an incident report include?', 'Your password', 'The time and what happened'),
        ('Which channel should you use for a suspected incident?', 'A public social post', 'An approved IT channel')]),
]


def rich_text(value):
    return [{'type': 'paragraph', 'children': [{'text': value}]}]


class Command(BaseCommand):
    help = 'Idempotently create a four-role demo and five security lessons with assessments.'

    def handle(self, *args, **kwargs):
        people = {}
        for username, role in [('demo_hr', 'hr'), ('demo_it', 'it'), ('demo_manager', 'manager'), ('demo_employee', 'employee'), ('training_editor', 'instructor')]:
            user, created = User.objects.get_or_create(username=username, defaults={'role': role, 'email': username + '@example.test'})
            if created:
                user.set_password(PASSWORD)
                user.save(update_fields=['password'])
            elif user.role != role:
                raise CommandError(f'{username} already exists with role {user.role}; resolve the demo account collision first.')
            people[role] = user
        course, _ = Course.objects.get_or_create(title='Security essentials', instructor=people['instructor'],
            defaults={'description': 'Five short lessons on accounts, phishing, devices, data and incident response.', 'is_published': True})
        for index, (title, content, quiz_title, questions) in enumerate(LESSONS):
            Chapter.objects.get_or_create(course=course, title=title, defaults={
                'order_index': index, 'visibility': 'public', 'content': rich_text(content)})
            quiz, _ = Quiz.objects.get_or_create(course=course, title=quiz_title,
                defaults={'is_published': True, 'description': 'Pass with at least 70% to complete onboarding.'})
            for order, (prompt, incorrect, correct) in enumerate(questions):
                question, created = Question.objects.get_or_create(quiz=quiz, order_index=order, defaults={
                    'question_type': Question.SINGLE_CHOICE, 'points': 1,
                    'body': {'prompt': rich_text(prompt), 'options': [
                        {'id': 'a', 'text': incorrect}, {'id': 'b', 'text': correct}], 'correct_option_ids': ['b']}})
                # Repair only the two exact erroneous demo fixtures. Keep authored
                # questions and all historical submissions unchanged.
                if index == 4 and order in (1, 2) and not created:
                    wrong_body = {'prompt': rich_text(prompt), 'options': [
                        {'id': 'a', 'text': correct}, {'id': 'b', 'text': incorrect}], 'correct_option_ids': ['b']}
                    if question.body == wrong_body:
                        question.body['correct_option_ids'] = ['a']
                        question.save(update_fields=['body'])
        if not Onboarding.objects.filter(employee=people['employee']).exists():
            create_onboarding(employee=people['employee'], manager=people['manager'], creator=people['hr'],
                job_title='Software Engineer', department='Engineering', start_date=timezone.localdate() + timedelta(days=7), course=course)
        self.stdout.write(self.style.SUCCESS('LaunchOps demo ready. Roles: demo_hr, demo_it, demo_manager, demo_employee; password: ' + PASSWORD))
