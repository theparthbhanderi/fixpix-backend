from django.urls import path
from .admin_views import AdminDashboardStatsView, UserManagementView, JobMonitorView, EmergencyCreateAdmin

urlpatterns = [
    path('emergency-create/', EmergencyCreateAdmin.as_view(), name='admin_emergency_create'),
    path('dashboard/', AdminDashboardStatsView.as_view(), name='admin_dashboard'),
    path('users/', UserManagementView.as_view(), name='admin_users'),
    path('users/<int:pk>/action/', UserManagementView.as_view(), name='admin_user_action'),
    path('jobs/', JobMonitorView.as_view(), name='admin_jobs'),
]
