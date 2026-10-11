from django.urls import path

from . import views
from . import live_internal

urlpatterns = [
    path('_internal/live/<str:room_code>/bootstrap/', live_internal.bootstrap),
    path('_internal/live/<str:room_code>/finish/', live_internal.finish),
    path('sessions/<str:room_code>/live-results/', live_internal.results),
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
    # live sessions (room lifecycle)
    path('quizzes/<int:quiz_id>/sessions/', views.session_create),
    path('sessions/<str:room_code>/', views.session_detail),
    path('sessions/<str:room_code>/start/', views.session_start),
    path('sessions/<str:room_code>/end/', views.session_end),
    # leaderboard (persistent, course-scoped)
    path('courses/<int:course_id>/leaderboard/', views.course_leaderboard),
]
