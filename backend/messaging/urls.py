from django.urls import path

from . import views

urlpatterns = [
    path('courses/<int:course_id>/messages/', views.message_list),
]
