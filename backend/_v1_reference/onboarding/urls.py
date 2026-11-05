from django.urls import path
from . import views

urlpatterns = [
    path('workflows/', views.onboardings),
    path('workflows/<int:pk>/', views.detail),
    path('steps/<int:pk>/', views.step_detail),
    path('steps/<int:pk>/complete/', views.complete),
    path('issues/<int:pk>/resolve/', views.resolve_issue),
    path('directory/', views.directory),
]
