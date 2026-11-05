from django.urls import path

from . import views

urlpatterns = [
    # quizzes
    path('courses/<int:course_id>/quizzes/', views.quiz_list),
    path('courses/<int:course_id>/quizzes/create/', views.quiz_create),
    path('quizzes/<int:quiz_id>/', views.quiz_detail),
    # questions
    path('quizzes/<int:quiz_id>/questions/', views.question_list),
    path('quizzes/<int:quiz_id>/questions/create/', views.question_create),
    path('questions/<int:question_id>/', views.question_detail),
    # student quiz-taking
    path('quizzes/<int:quiz_id>/take/', views.quiz_take),
    path('quizzes/<int:quiz_id>/submit/', views.quiz_submit),
    path('quizzes/<int:quiz_id>/my-result/', views.quiz_my_result),
    # leaderboard (persistent, course-scoped)
    path('courses/<int:course_id>/leaderboard/', views.course_leaderboard),
]
