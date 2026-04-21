from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions.models import DailyUsage, UserPlan


class Command(BaseCommand):
    help = "Resets daily usage snapshots and prunes old rows for abuse-safe operations"

    def handle(self, *args, **options):
        today = timezone.localdate()
        stale_days = 90

        # Keep UserPlan counters aligned with today's usage rows.
        updated = 0
        for user_plan in UserPlan.objects.all().iterator():
            usage = DailyUsage.objects.filter(user=user_plan.user, date=today).first()
            new_daily = usage.edits_used if usage else 0
            if user_plan.daily_usage != new_daily:
                user_plan.daily_usage = new_daily
                user_plan.last_reset_date = timezone.now()
                user_plan.save(update_fields=["daily_usage", "last_reset_date", "updated_at"])
                updated += 1

        # Trim historical daily usage data to keep table lean.
        cutoff = today - timedelta(days=stale_days)
        deleted, _ = DailyUsage.objects.filter(date__lt=cutoff).delete()

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} user plans; deleted {deleted} stale daily usage rows."))
