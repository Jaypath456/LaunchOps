from django.urls import path, include

from onboarding import views as onboarding_views

urlpatterns = [
    path('api/auth/', include('users.urls')),
    path('api/onboarding/', include('onboarding.urls')),
    path('healthz', onboarding_views.healthz),
]
