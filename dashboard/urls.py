from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('setup/', views.SetupView.as_view(), name='setup'),
    path('api/sessions/', views.SessionCreateAPIView.as_view(), name='api-session-create'),
]
