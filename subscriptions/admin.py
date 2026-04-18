from django.contrib import admin
from .models import SubscriptionPlan, UserSubscription, UsageLog

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
