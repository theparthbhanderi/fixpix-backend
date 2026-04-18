from .models import UserSubscription, SubscriptionPlan, UsageLog
from .constants import PLAN_LIMITS
from django.utils import timezone
from datetime import date

def get_user_plan(user):
    """
    Returns the user's current active plan name (default 'free').
    """
    if not user.is_authenticated:
        return 'free'
    
    try:
        sub = UserSubscription.objects.get(user=user, is_active=True)
        if sub.expires_at and sub.expires_at < timezone.now():
            # Plan expired
            return 'free'
        return sub.plan.name
    except UserSubscription.DoesNotExist:
        return 'free'

def check_and_log_usage(user, feature_key):
    """
    Checks if a user is allowed to use a feature based on their plan limits.
    If allowed, logs the usage and returns success.
    """
    if not user.is_authenticated:
        return {"allowed": False, "reason": "auth_required"}

    plan_name = get_user_plan(user)
    limit = PLAN_LIMITS.get(plan_name, {}).get(feature_key, 0)

    if limit == -1:
        # Unlimited
        UsageLog.objects.create(user=user, feature_key=feature_key)
        return {"allowed": True, "remaining": float('inf')}

    if limit == 0:
        return {"allowed": False, "reason": "feature_not_available_on_plan", "limit": 0}

    # Count today's usage
    today = date.today()
    count = UsageLog.objects.filter(user=user, feature_key=feature_key, date=today).count()

    if count >= limit:
        return {
            "allowed": False, 
            "reason": "daily_limit_reached", 
            "limit": limit, 
            "used": count
        }

    # Allow and log
    UsageLog.objects.create(user=user, feature_key=feature_key)
    return {"allowed": True, "remaining": limit - count - 1}

def get_usage_stats(user):
    """
    Returns usage stats for all features for today.
    """
    if not user.is_authenticated:
        return {}

    plan_name = get_user_plan(user)
    plan_limits = PLAN_LIMITS.get(plan_name, {})
    today = date.today()
    
    stats = {}
    for feature_key, limit in plan_limits.items():
        used = UsageLog.objects.filter(user=user, feature_key=feature_key, date=today).count()
        stats[feature_key] = {
            "used": used,
            "limit": limit,
            "remaining": (limit - used) if limit != -1 else -1
        }
    
    return stats
