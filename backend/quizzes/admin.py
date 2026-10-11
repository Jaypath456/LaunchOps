from django.contrib import admin

from .models import Question, Quiz


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'is_published')
    list_filter = ('is_published',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('quiz', 'question_type', 'points', 'order_index')
    list_filter = ('question_type',)
