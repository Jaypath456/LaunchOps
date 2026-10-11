from django.urls import path

from . import views

urlpatterns = [
    # terms
    path('terms/', views.term_list),
    # sections
    path('courses/<int:course_id>/sections/', views.section_list),
    path('courses/<int:course_id>/sections/create/', views.section_create),
    path('sections/<int:section_id>/', views.section_detail),
    # breaks (student-owned)
    path('breaks/', views.break_list_create),
    path('breaks/<int:break_id>/', views.break_delete),
    # schedule generation (common to both roles)
    path('schedule/generate/', views.generate_schedule),
    # saved schedules (student's chosen candidate)
    path('schedule/saved/', views.saved_schedule_list_create),
    path('schedule/saved/<int:saved_schedule_id>/confirm/', views.saved_schedule_confirm),
]
