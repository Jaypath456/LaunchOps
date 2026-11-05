from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('quizzes', '0005_livesession_engine')]

    operations = [
        migrations.DeleteModel(name='LiveSession'),
        migrations.RemoveField(model_name='question', name='time_limit_seconds'),
    ]
