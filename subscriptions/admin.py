from django.contrib import admin
from .models import DailyUsage, SubscriptionPlan, UsageEventLog, UserSubscription, UsageLog, UserPlan

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_name', 'price_inr', 'razorpay_plan_id')

@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'started_at', 'expires_at', 'is_active')
    list_filter = ('plan', 'is_active')
    search_fields = ('user__username', 'user__email')

@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'feature_key', 'used_at', 'date')
    list_filter = ('feature_key', 'date')
    search_fields = ('user__username',)


@admin.register(UserPlan)
class UserPlanAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'daily_usage', 'last_reset_date', 'updated_at')
    list_filter = ('plan',)
    search_fields = ('user__username', 'user__email')


@admin.register(DailyUsage)
class DailyUsageAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'date',
        'edits_used',
        'upscale_used',
        'bg_remove_used',
        'abuse_score',
        'is_hard_blocked',
        'blocked_until',
    )
    list_filter = ('date', 'is_hard_blocked')
    search_fields = ('user__username', 'user__email', 'last_ip')


@admin.register(UsageEventLog)
class UsageEventLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'feature_used', 'cost', 'status', 'response_time_ms', 'created_at')
    list_filter = ('plan', 'feature_used', 'status', 'created_at')
    search_fields = ('user__username', 'user__email', 'ip_address')
