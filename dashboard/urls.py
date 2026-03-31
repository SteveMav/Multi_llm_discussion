from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('setup/', views.SetupView.as_view(), name='setup'),
    path('api/sessions/', views.SessionCreateAPIView.as_view(), name='api-session-create'),
    path('session/<int:session_id>/roundtable/', views.RoundtableView.as_view(), name='roundtable'),
    path('api/roundtable/', views.RoundtableConfigAPIView.as_view(), name='api-roundtable-config'),
]
