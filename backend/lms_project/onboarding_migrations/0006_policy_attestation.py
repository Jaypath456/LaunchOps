from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('onboarding', '0005_assessmentattempt_issue_lessonreview'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='assessmentattempt',
            name='onboarding',
        ),
        migrations.RemoveField(
            model_name='assessmentattempt',
            name='quiz',
        ),
        migrations.RemoveConstraint(
            model_name='lessonreview',
            name='unique_lesson_review',
        ),
        migrations.RemoveField(
            model_name='onboarding',
            name='training_course',
        ),
        migrations.DeleteModel(
            name='AssessmentAttempt',
        ),
        migrations.DeleteModel(
            name='LessonReview',
        ),
    ]
