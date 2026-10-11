from django.urls import path
from . import views

urlpatterns = [
    # courses
    path('courses/', views.course_list),
    path('courses/mine/', views.instructor_courses),
    path('courses/create/', views.course_create),
    path('courses/<int:course_id>/', views.course_detail),
    path('courses/<int:course_id>/enroll/', views.manage_enrollment),
    path('courses/<int:course_id>/enrollment-status/', views.enrollment_status, name='enrollment-status'),
    path('courses/<int:course_id>/students/', views.course_enrolled_students),
    # chapters
    path('courses/<int:course_id>/chapters/', views.chapter_list),
    path('courses/<int:course_id>/chapters/create/', views.chapter_create),
    path('chapters/<int:chapter_id>/', views.chapter_detail),
    # student
    path('my-courses/', views.my_courses),
]
