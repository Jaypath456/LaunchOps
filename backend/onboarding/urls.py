from django.urls import path
from . import views

urlpatterns = [
    path('workflows/', views.onboardings),
    path('workflows/<int:pk>/', views.detail),
    path('steps/<int:pk>/', views.step_detail),
    path('steps/<int:pk>/complete/', views.complete),
    path('lessons/<int:chapter_id>/review/', views.lesson_review),
    path('issues/<int:pk>/resolve/', views.resolve_issue),
    path('directory/', views.directory),
    path('assessments/<int:quiz_id>/retry/', views.retry_assessment),
]
