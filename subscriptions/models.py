from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class SubscriptionPlan(models.Model):
    PLAN_CHOICES = [
        ('free', 'Free'),
        ('pro', 'Pro'),
        ('elite', 'Elite'),
        ('pro_yearly', 'Pro Yearly'),
        ('elite_yearly', 'Elite Yearly'),
    ]
    name = models.CharField(max_length=50, choices=PLAN_CHOICES, unique=True)
    display_name = models.CharField(max_length=100)
    price_inr = models.IntegerField(default=0)
    razorpay_plan_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return self.display_name

class UserSubscription(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    razorpay_subscription_id = models.CharField(max_length=255, null=True, blank=True)
    razorpay_payment_id = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.display_name}"

class UsageLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='usage_logs')
    feature_key = models.CharField(max_length=100)
    used_at = models.DateTimeField(auto_now_add=True)
    date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} used {self.feature_key} at {self.used_at}"


class UserPlan(models.Model):
    PLAN_CHOICES = [
        ('FREE', 'Free'),
        ('PRO', 'Pro'),
        ('ELITE', 'Elite'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='user_plan')
    plan = models.CharField(max_length=10, choices=PLAN_CHOICES, default='FREE')
    daily_usage = models.PositiveIntegerField(default=0)
    last_reset_date = models.DateTimeField(default=timezone.now)
    features = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan}"


class DailyUsage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_usage_rows')
    date = models.DateField()
    edits_used = models.PositiveIntegerField(default=0)
    upscale_used = models.PositiveIntegerField(default=0)
    bg_remove_used = models.PositiveIntegerField(default=0)
    last_request_at = models.DateTimeField(null=True, blank=True)
    rapid_request_count = models.PositiveIntegerField(default=0)
    abuse_score = models.PositiveIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)
    is_hard_blocked = models.BooleanField(default=False)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_image_hash = models.CharField(max_length=64, null=True, blank=True)
    same_image_repeat_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'date')
        indexes = [
            models.Index(fields=['user', 'date']),
            models.Index(fields=['date']),
            models.Index(fields=['blocked_until']),
        ]

    def __str__(self):
        return f"{self.user.username} usage {self.date}"


class UsageEventLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='usage_events')
    plan = models.CharField(max_length=16)
    feature_used = models.CharField(max_length=100)
    cost = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    response_time_ms = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=16, default='allowed')
    notes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['plan', 'feature_used']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.user.username} {self.feature_used} {self.status}"
