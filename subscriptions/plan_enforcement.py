from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
import hashlib
import logging
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError, transaction
from django.utils import timezone

from .constants import PLAN_CONFIG, PLAN_NAME_TO_TIER
from .models import DailyUsage, UsageEventLog, UsageLog, UserPlan, UserSubscription


logger = logging.getLogger(__name__)


FEATURE_USAGE_KEYS = {
    "edit": "edit",
    "background_remove": "background_remove",
    "upscaling": "upscaling",
}

RATE_LIMITS_PER_MINUTE = {
    "FREE": 10,
    "PRO": 30,
    "ELITE": 120,
}

FREE_IP_RATE_LIMIT_PER_MINUTE = 20

CONCURRENCY_LIMITS = {
    "FREE": 2,
    "PRO": 5,
    "ELITE": None,
}

MAX_COST_PER_REQUEST = {
    "FREE": Decimal("0.01"),
    "PRO": Decimal("0.05"),
    "ELITE": None,
}

FEATURE_COST_ESTIMATES = {
    "edit": Decimal("0.008"),
    "background_remove": Decimal("0.010"),
    "upscaling": Decimal("0.020"),
    "text_to_image": Decimal("0.012"),
    "style_transfer": Decimal("0.018"),
}

HARD_BLOCK_THRESHOLD = 25
TEMP_BLOCK_DURATION = timedelta(minutes=15)
RAPID_REQUEST_WINDOW_SECONDS = 4
MAX_SAME_IMAGE_REPEAT = 6


def _normalize_plan_name(plan_name: str) -> str:
    if not plan_name:
        return "FREE"
    normalized = PLAN_NAME_TO_TIER.get(str(plan_name).lower())
    return normalized or "FREE"


def resolve_user_plan(user) -> str:
    if not user or not user.is_authenticated:
        return "FREE"

    subscription = (
        UserSubscription.objects.filter(user=user, is_active=True)
        .select_related("plan")
        .order_by("-started_at")
        .first()
    )
    if not subscription:
        return "FREE"

    if subscription.expires_at and subscription.expires_at < timezone.now():
        return "FREE"

    raw_plan = str(getattr(subscription.plan, "name", "")).upper()
    if raw_plan in ("PRO", "PRO_YEARLY"):
        return "PRO"
    if raw_plan in ("ELITE", "ELITE_YEARLY"):
        return "ELITE"
    return "FREE"


def _resolve_plan_from_subscription(user) -> str:
    # Backward-compatible internal name kept for existing callers.
    return resolve_user_plan(user)


def _build_features_snapshot(plan_tier: str) -> dict:
    return deepcopy(PLAN_CONFIG.get(plan_tier, PLAN_CONFIG["FREE"]))


def _should_reset_daily_usage(last_reset_date) -> bool:
    if not last_reset_date:
        return True
    return timezone.now() - last_reset_date >= timedelta(hours=24)


def _fallback_user_plan_state(user, plan_tier: str):
    daily_usage = UsageLog.objects.filter(user=user, date=timezone.localdate()).count()
    return SimpleNamespace(
        user=user,
        plan=plan_tier,
        features=_build_features_snapshot(plan_tier),
        daily_usage=daily_usage,
        last_reset_date=timezone.now(),
    )


def _fallback_daily_usage_state(user):
    today = timezone.localdate()
    edits = UsageLog.objects.filter(user=user, date=today).count()
    return SimpleNamespace(
        user=user,
        date=today,
        edits_used=edits,
        upscale_used=UsageLog.objects.filter(user=user, date=today, feature_key="upscaling").count(),
        bg_remove_used=UsageLog.objects.filter(user=user, date=today, feature_key="background_remove").count(),
        rapid_request_count=0,
        abuse_score=0,
        blocked_until=None,
        is_hard_blocked=False,
        last_ip=None,
        last_image_hash=None,
        same_image_repeat_count=0,
        last_request_at=None,
    )


def get_or_create_user_plan(user):
    if not user or not user.is_authenticated:
        return None

    plan_tier = _resolve_plan_from_subscription(user)
    defaults = {
        "plan": plan_tier,
        "features": _build_features_snapshot(plan_tier),
    }

    try:
        user_plan, _ = UserPlan.objects.get_or_create(user=user, defaults=defaults)
    except (OperationalError, ProgrammingError):
        # Keep enforcement alive even if sync table is unavailable.
        return _fallback_user_plan_state(user, plan_tier)

    needs_update = False
    if user_plan.plan != plan_tier:
        user_plan.plan = plan_tier
        needs_update = True

    expected_features = _build_features_snapshot(plan_tier)
    if user_plan.features != expected_features:
        user_plan.features = expected_features
        needs_update = True

    if _should_reset_daily_usage(user_plan.last_reset_date):
        user_plan.daily_usage = 0
        user_plan.last_reset_date = timezone.now()
        needs_update = True

    if needs_update:
        user_plan.save(update_fields=["plan", "features", "daily_usage", "last_reset_date", "updated_at"])

    return user_plan


def get_today_usage(user):
    today = timezone.localdate()
    try:
        usage, _ = DailyUsage.objects.get_or_create(user=user, date=today)
        return usage
    except (OperationalError, ProgrammingError):
        return _fallback_daily_usage_state(user)


def _get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def _rate_key(prefix: str, value: str) -> str:
    minute_bucket = timezone.now().strftime("%Y%m%d%H%M")
    return f"rate:{prefix}:{value}:{minute_bucket}"


def _increment_rate_counter(key: str, timeout: int = 70) -> int:
    current = cache.get(key)
    if current is None:
        cache.set(key, 1, timeout=timeout)
        return 1
    try:
        current = cache.incr(key)
    except ValueError:
        current = int(current) + 1
        cache.set(key, current, timeout=timeout)
    return int(current)


def _estimated_cost(feature_key: str, batch_count: int = 1) -> Decimal:
    normalized = feature_key or "edit"
    base_cost = FEATURE_COST_ESTIMATES.get(normalized, Decimal("0.008"))
    safe_batch = max(1, int(batch_count or 1))
    return base_cost * Decimal(str(safe_batch))


def _active_jobs_for_user(user) -> int:
    try:
        from api.models import ImageProject

        return ImageProject.objects.filter(user=user, status__in=["pending", "processing"]).count()
    except Exception:
        return 0


def _extract_image_hash(request):
    for file_key in ("image", "file", "style_image", "mask", "mask_file"):
        file_obj = request.FILES.get(file_key)
        if not file_obj:
            continue
        try:
            pos = file_obj.tell()
            file_obj.seek(0)
            digest = hashlib.sha256(file_obj.read()).hexdigest()
            file_obj.seek(pos)
            return digest
        except Exception:
            continue

    request_data = getattr(request, "data", None)
    if request_data is None:
        request_data = getattr(request, "POST", {})

    inline_image = request_data.get("image") if request_data else None
    if isinstance(inline_image, str) and inline_image:
        payload = inline_image.split("base64,", 1)[-1][:4000]
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return None


def _bump_abuse_score(usage, points: int, reason: str):
    usage.abuse_score = int(usage.abuse_score or 0) + int(points)
    if usage.abuse_score >= HARD_BLOCK_THRESHOLD:
        usage.is_hard_blocked = True
        usage.blocked_until = timezone.now() + timedelta(days=3650)
    elif usage.abuse_score >= 10:
        usage.blocked_until = timezone.now() + TEMP_BLOCK_DURATION

    if hasattr(usage, "save"):
        usage.save(update_fields=["abuse_score", "is_hard_blocked", "blocked_until", "updated_at"])

    return {
        "allowed": False,
        "reason": reason,
        "status_code": 429,
        "error": "Suspicious request pattern detected. Please retry later or upgrade for trusted priority access.",
    }


def _track_request_pattern(usage, ip_address: str, image_hash: str):
    now = timezone.now()
    suspicious = False

    if usage.last_request_at and (now - usage.last_request_at).total_seconds() <= RAPID_REQUEST_WINDOW_SECONDS:
        usage.rapid_request_count = int(usage.rapid_request_count or 0) + 1
    else:
        usage.rapid_request_count = 1

    if image_hash and usage.last_image_hash == image_hash:
        usage.same_image_repeat_count = int(usage.same_image_repeat_count or 0) + 1
    elif image_hash:
        usage.same_image_repeat_count = 1

    if (
        usage.same_image_repeat_count > MAX_SAME_IMAGE_REPEAT
        or usage.rapid_request_count > 20
        or (usage.rapid_request_count > 8 and usage.same_image_repeat_count > 3)
    ):
        suspicious = True

    usage.last_request_at = now
    usage.last_ip = ip_address
    if image_hash:
        usage.last_image_hash = image_hash

    if hasattr(usage, "save"):
        usage.save(
            update_fields=[
                "rapid_request_count",
                "same_image_repeat_count",
                "last_request_at",
                "last_ip",
                "last_image_hash",
                "updated_at",
            ]
        )

    return suspicious


def _count_today_usage(user, usage_key: str) -> int:
    return UsageLog.objects.filter(
        user=user,
        feature_key=usage_key,
        date=timezone.localdate(),
    ).count()


def _validate_feature_limit(user, plan_config: dict, feature_key: str):
    usage = get_today_usage(user)

    if feature_key == "background_remove":
        limit = plan_config.get("backgroundRemovalLimit")
        if limit != "unlimited":
            used = int(getattr(usage, "bg_remove_used", 0))
            if used >= int(limit):
                return {
                    "allowed": False,
                    "reason": "background_removal_daily_limit_reached",
                    "limit": int(limit),
                    "used": used,
                }

    if feature_key == "upscaling":
        limit = plan_config.get("upscalingLimit")
        if limit in (0, "0"):
            return {
                "allowed": False,
                "reason": "upscaling_not_available_on_plan",
                "limit": 0,
                "used": 0,
            }

        if limit != "unlimited":
            used = int(getattr(usage, "upscale_used", 0))
            if used >= int(limit):
                return {
                    "allowed": False,
                    "reason": "upscaling_daily_limit_reached",
                    "limit": int(limit),
                    "used": used,
                }

    return {"allowed": True}


def _queue_for_plan(plan_tier: str) -> str:
    if plan_tier == "ELITE":
        return "elite_fastest"
    if plan_tier == "PRO":
        return "pro_fast"
    return "free_low"


def _priority_for_plan(plan_tier: str) -> str:
    if plan_tier == "ELITE":
        return "fastest"
    if plan_tier == "PRO":
        return "faster"
    return "low"


def _build_user_plan_payload(user_plan, plan_config: dict) -> dict:
    usage = get_today_usage(user_plan.user)
    return {
        "plan": user_plan.plan,
        "dailyUsage": int(getattr(usage, "edits_used", user_plan.daily_usage)),
        "lastResetDate": user_plan.last_reset_date,
        "watermark": plan_config.get("watermark", False),
        "maxTasksPerDay": plan_config.get("maxTasksPerDay", 0),
        "backgroundRemovalLimit": plan_config.get("backgroundRemovalLimit", 0),
        "upscalingLimit": plan_config.get("upscalingLimit", 0),
        "batchProcessing": plan_config.get("batchProcessing", False),
        "priorityProcessing": plan_config.get("priorityProcessing", False),
        "modelAccess": plan_config.get("modelAccess", "basic"),
    }


def _save_usage_if_model(instance, update_fields):
    if hasattr(instance, "save"):
        instance.save(update_fields=update_fields)


def _feature_counter_field(feature_key):
    if feature_key == "upscaling":
        return "upscale_used"
    if feature_key == "background_remove":
        return "bg_remove_used"
    return "edits_used"


def _log_usage_event(user, plan, feature_key, cost, status, request=None, notes=None, response_time_ms=0):
    try:
        UsageEventLog.objects.create(
            user=user,
            plan=plan,
            feature_used=feature_key or "edit",
            cost=Decimal(str(cost or 0)),
            response_time_ms=max(0, int(response_time_ms or 0)),
            ip_address=_get_client_ip(request) if request else None,
            status=status,
            notes=notes or {},
        )
    except Exception:
        # Analytics logging must never break request path.
        pass


def record_successful_usage(request, feature_key=None, cost_estimate=None, notes=None):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return

    if getattr(request, "_usage_committed", False):
        return

    plan_tier = getattr(request, "plan", None) or resolve_user_plan(user)
    usage = get_today_usage(user)
    user_plan = get_or_create_user_plan(user)
    feature = feature_key or getattr(request, "_usage_feature_key", None) or "edit"
    cost = Decimal(str(cost_estimate or getattr(request, "_usage_cost_estimate", _estimated_cost(feature))))

    usage.edits_used = int(getattr(usage, "edits_used", 0)) + 1
    counter_field = _feature_counter_field(feature)
    if counter_field != "edits_used":
        setattr(usage, counter_field, int(getattr(usage, counter_field, 0)) + 1)

    _save_usage_if_model(usage, ["edits_used", counter_field, "updated_at"] if counter_field != "edits_used" else ["edits_used", "updated_at"])

    usage_log_key = FEATURE_USAGE_KEYS.get(feature, "task_request")
    UsageLog.objects.create(user=user, feature_key=usage_log_key)

    if user_plan is not None and hasattr(user_plan, "save"):
        user_plan.daily_usage = int(getattr(usage, "edits_used", user_plan.daily_usage))
        user_plan.save(update_fields=["daily_usage", "updated_at"])

    _log_usage_event(
        user=user,
        plan=plan_tier,
        feature_key=feature,
        cost=cost,
        status="allowed",
        request=request,
        notes=notes,
        response_time_ms=0,
    )
    logger.info("USER PLAN: %s | FEATURE: %s | EST_COST: %s", plan_tier, feature, str(cost))
    request._usage_committed = True


def check_user_plan_for_request(request, feature_key=None, batch_count=1, consume=False, estimated_cost=None):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "allowed": False,
            "reason": "auth_required",
            "status_code": 401,
        }

    with transaction.atomic():
        normalized_feature = feature_key or "edit"
        user_plan = get_or_create_user_plan(user)
        usage = get_today_usage(user)
        plan_config = deepcopy(user_plan.features or _build_features_snapshot(user_plan.plan))
        request.plan = user_plan.plan
        request.plan_config = deepcopy(plan_config)
        request._usage_feature_key = normalized_feature

        if getattr(usage, "is_hard_blocked", False):
            _log_usage_event(user, user_plan.plan, normalized_feature, 0, "hard_blocked", request, {"abuse_score": getattr(usage, "abuse_score", 0)})
            return {
                "allowed": False,
                "reason": "hard_blocked",
                "status_code": 403,
                "error": "Account blocked due to repeated abuse patterns.",
            }

        if getattr(usage, "blocked_until", None) and usage.blocked_until > timezone.now():
            _log_usage_event(user, user_plan.plan, normalized_feature, 0, "temp_blocked", request, {"blocked_until": usage.blocked_until.isoformat()})
            return {
                "allowed": False,
                "reason": "temporarily_blocked",
                "status_code": 429,
                "error": "Too many suspicious attempts. Please wait before trying again.",
                "blockedUntil": usage.blocked_until,
            }

        user_rate_limit = RATE_LIMITS_PER_MINUTE.get(user_plan.plan, RATE_LIMITS_PER_MINUTE["FREE"])
        user_rate_key = _rate_key("user", str(user.id))
        user_rate_count = _increment_rate_counter(user_rate_key)
        if user_rate_count > user_rate_limit:
            _log_usage_event(user, user_plan.plan, normalized_feature, 0, "rate_limited", request, {"limit": user_rate_limit, "requests": user_rate_count})
            return {
                "allowed": False,
                "reason": "too_many_requests",
                "status_code": 429,
                "error": "Too many requests in the last minute.",
                "limit": user_rate_limit,
                "requests": user_rate_count,
            }

        ip = _get_client_ip(request)
        if user_plan.plan == "FREE":
            ip_key = _rate_key("ip", ip)
            ip_count = _increment_rate_counter(ip_key)
            if ip_count > FREE_IP_RATE_LIMIT_PER_MINUTE:
                _bump_abuse_score(usage, 2, "ip_abuse_detected")
                _log_usage_event(user, user_plan.plan, normalized_feature, 0, "ip_throttled", request, {"ip": ip, "requests": ip_count})
                return {
                    "allowed": False,
                    "reason": "ip_throttled",
                    "status_code": 429,
                    "error": "Too many free-tier requests from this IP. Try again shortly.",
                }

        active_jobs = _active_jobs_for_user(user)
        concurrency_limit = CONCURRENCY_LIMITS.get(user_plan.plan)
        if concurrency_limit is not None and active_jobs >= concurrency_limit:
            _log_usage_event(user, user_plan.plan, normalized_feature, 0, "concurrency_throttled", request, {"active_jobs": active_jobs, "limit": concurrency_limit})
            return {
                "allowed": False,
                "reason": "concurrency_limit_reached",
                "status_code": 429,
                "error": "Too many active jobs. Please wait for current jobs to finish.",
                "activeJobs": active_jobs,
                "limit": concurrency_limit,
                "queue": _queue_for_plan(user_plan.plan),
            }

        estimated = Decimal(str(estimated_cost if estimated_cost is not None else _estimated_cost(normalized_feature, batch_count)))
        request._usage_cost_estimate = estimated
        max_allowed_cost = MAX_COST_PER_REQUEST.get(user_plan.plan)
        if max_allowed_cost is not None and estimated > max_allowed_cost:
            _log_usage_event(user, user_plan.plan, normalized_feature, estimated, "cost_blocked", request, {"cap": str(max_allowed_cost)})
            return {
                "allowed": False,
                "reason": "cost_limit_exceeded",
                "status_code": 403,
                "error": "Request is too expensive for your plan.",
                "estimatedCost": str(estimated),
                "maxCost": str(max_allowed_cost),
            }

        image_hash = _extract_image_hash(request)
        suspicious_pattern = _track_request_pattern(usage, ip, image_hash)
        if suspicious_pattern:
            block_result = _bump_abuse_score(usage, 3, "suspicious_pattern_detected")
            _log_usage_event(user, user_plan.plan, normalized_feature, 0, "abuse_temp_block", request, {"abuse_score": usage.abuse_score})
            return {
                **block_result,
                "plan": user_plan.plan,
                "plan_config": plan_config,
            }

        if batch_count and batch_count > 1 and not plan_config.get("batchProcessing", False):
            return {
                "allowed": False,
                "reason": "batch_processing_not_available_on_plan",
                "status_code": 403,
                "plan": user_plan.plan,
                "plan_config": plan_config,
            }

        # Explicit backend cap for FREE tier as a hard guardrail.
        if user_plan.plan == "FREE" and int(getattr(usage, "edits_used", 0)) >= 5:
            _log_usage_event(user, user_plan.plan, normalized_feature, estimated, "daily_limit", request)
            return {
                "allowed": False,
                "reason": "limit_reached",
                "error": "You've reached today's limit. Upgrade to continue instantly",
                "upgradeMessage": "You've reached today's limit. Upgrade to continue instantly",
                "upgradeCTA": "Upgrade to Pro",
                "status_code": 403,
                "limit": 5,
                "used": int(getattr(usage, "edits_used", 0)),
                "plan": user_plan.plan,
                "plan_config": plan_config,
            }

        max_tasks_per_day = plan_config.get("maxTasksPerDay", 0)
        if max_tasks_per_day != "unlimited" and int(getattr(usage, "edits_used", 0)) >= int(max_tasks_per_day):
            _log_usage_event(user, user_plan.plan, normalized_feature, estimated, "daily_limit", request)
            return {
                "allowed": False,
                "reason": "daily_task_limit_reached",
                "status_code": 429,
                "limit": int(max_tasks_per_day),
                "used": int(getattr(usage, "edits_used", 0)),
                "plan": user_plan.plan,
                "plan_config": plan_config,
            }

        if feature_key:
            feature_check = _validate_feature_limit(user, plan_config, feature_key)
            if not feature_check.get("allowed"):
                _log_usage_event(user, user_plan.plan, normalized_feature, estimated, "feature_limit", request, feature_check)
                feature_check["status_code"] = 429
                feature_check["plan"] = user_plan.plan
                feature_check["plan_config"] = plan_config
                return feature_check

        if consume:
            record_successful_usage(request, feature_key=normalized_feature, cost_estimate=estimated)

    request.user_plan_tier = user_plan.plan
    request.plan_config = plan_config
    request.processing_priority = _priority_for_plan(user_plan.plan)
    request.processing_queue = _queue_for_plan(user_plan.plan)
    request.userPlan = _build_user_plan_payload(user_plan, plan_config)

    return {
        "allowed": True,
        "plan": user_plan.plan,
        "plan_config": plan_config,
        "userPlan": request.userPlan,
        "priority": request.processing_priority,
        "queue": request.processing_queue,
        "dailyUsage": int(getattr(usage, "edits_used", user_plan.daily_usage)),
        "estimatedCost": str(estimated),
        "abuseScore": int(getattr(usage, "abuse_score", 0)),
    }


def checkUserPlan(user_id):
    """
    checkUserPlan(userId) compatibility helper.
    Returns plan metadata used by backend request middleware/decorators.
    """
    user = User.objects.filter(id=user_id).first()
    if not user:
        return {"allowed": False, "reason": "user_not_found"}

    user_plan = get_or_create_user_plan(user)
    return {
        "allowed": True,
        "userId": str(user.id),
        **_build_user_plan_payload(user_plan, deepcopy(user_plan.features)),
        "features": deepcopy(user_plan.features),
        "priority": _priority_for_plan(user_plan.plan),
        "queue": _queue_for_plan(user_plan.plan),
    }
