from django.urls import path
from onboarding import auth

urlpatterns = [
    path('login/', auth.login),
    path('refresh/', auth.SessionRefreshView.as_view()),
    path('me/', auth.me),
]
