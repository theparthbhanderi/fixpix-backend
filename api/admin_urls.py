from django.urls import path
from .admin_views import (
    AdminLoginView,
    AdminTokenVerifyView,
    AdminDashboardStatsView,
    UserManagementView,
    JobMonitorView,
    AdminAnalyticsView,
    AdminActivityLogView,
    AdminAIInsightsView,
    AdminSystemHealthView,
    AdminUserDetailView,
    EmergencyCreateAdmin,
)

urlpatterns = [
    path('login/', AdminLoginView.as_view(), name='admin-login'),
    path('verify/', AdminTokenVerifyView.as_view(), name='admin-verify'),
    path('dashboard/', AdminDashboardStatsView.as_view(), name='admin-dashboard'),
    path('users/', UserManagementView.as_view(), name='admin-users'),
    path('users/<int:pk>/detail/', AdminUserDetailView.as_view(), name='admin-user-detail'),
    path('jobs/', JobMonitorView.as_view(), name='admin-jobs'),
    path('analytics/', AdminAnalyticsView.as_view(), name='admin-analytics'),
    path('activity/', AdminActivityLogView.as_view(), name='admin-activity'),
    path('insights/', AdminAIInsightsView.as_view(), name='admin-insights'),
    path('system-health/', AdminSystemHealthView.as_view(), name='admin-system-health'),
    path('emergency-create/', EmergencyCreateAdmin.as_view(), name='admin-emergency'),
]
