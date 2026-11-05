from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login),
    path('refresh/', views.SessionRefreshView.as_view()),
    path('me/', views.me),
]
