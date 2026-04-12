from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('setup/', views.SetupView.as_view(), name='setup'),
    path('api/sessions/', views.SessionCreateAPIView.as_view(), name='api-session-create'),
    path('session/<int:session_id>/roundtable/', views.RoundtableView.as_view(), name='roundtable'),
    path('api/roundtable/', views.RoundtableConfigAPIView.as_view(), name='api-roundtable-config'),
    path('api/session/<int:session_id>/abort/', views.AbortSessionAPIView.as_view(), name='api-session-abort'),
    path("session/<int:session_id>/cockpit/", views.cockpit_view, name="cockpit"),
    path("session/<int:session_id>/stream/", views.stream_debate, name="stream-debate"),
    path("session/<int:session_id>/report/pdf/", views.download_pdf_report, name="download-pdf-report"),
]
