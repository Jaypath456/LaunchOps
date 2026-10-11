from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('quizzes', '0004_question_time_limit_seconds')]
    operations = [
        migrations.AddField(
            model_name='livesession', name='engine',
            field=models.CharField(max_length=10, choices=[('django', 'Django'), ('go', 'Go')], default='django'),
        ),
    ]
