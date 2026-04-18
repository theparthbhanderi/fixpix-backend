"""
Admin Activity Logging Models for FixPix Admin Panel.

Tracks all admin actions for security auditing.
"""

from django.db import models
from django.contrib.auth.models import User
import uuid


class AdminActivityLog(models.Model):
    """
    Immutable audit log for all admin panel actions.
    Every admin API call gets logged here.
    """
    ACTION_CHOICES = [
        ('login', 'Admin Login'),
        ('login_failed', 'Failed Login Attempt'),
        ('logout', 'Admin Logout'),
        ('view_dashboard', 'Viewed Dashboard'),
        ('view_users', 'Viewed Users List'),
        ('view_analytics', 'Viewed Analytics'),
        ('ban_user', 'Banned User'),
        ('unban_user', 'Unbanned User'),
        ('promote_user', 'Promoted User to Staff'),
        ('view_jobs', 'Viewed Jobs'),
        ('view_activity', 'Viewed Activity Logs'),
        ('export_data', 'Exported Data'),
        ('other', 'Other Action'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admin_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_actions'
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    target_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_actions_received'
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Admin Activity Log'
        verbose_name_plural = 'Admin Activity Logs'
        indexes = [
            models.Index(fields=['action', 'timestamp']),
            models.Index(fields=['admin_user', 'timestamp']),
        ]

    def __str__(self):
        admin_name = self.admin_user.username if self.admin_user else 'Unknown'
        return f"[{self.timestamp}] {admin_name}: {self.action}"

    @classmethod
    def log(cls, request, action, target_user=None, details=None):
        """
        Convenience method to log an admin action from a request.
        """
        ip = cls._get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        return cls.objects.create(
            admin_user=request.user if request.user.is_authenticated else None,
            action=action,
            target_user=target_user,
            ip_address=ip,
            user_agent=user_agent,
            details=details or {},
        )

    @staticmethod
    def _get_client_ip(request):
        """Extract real client IP, considering proxies."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
