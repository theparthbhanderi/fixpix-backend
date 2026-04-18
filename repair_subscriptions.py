import os
import django
import sys
from django.utils import timezone

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from subscriptions.models import UserSubscription
from api.services.supabase_service import supabase_service

def repair_all_subscriptions():
    print("🚀 Starting User Subscription Repair Sync...")
    active_subs = UserSubscription.objects.filter(is_active=True)
    
    count = 0
    for sub in active_subs:
        try:
            # Reconstruct Supabase UUID
            raw_username = sub.user.username
            if raw_username.startswith('sb_'):
                c = raw_username.replace('sb_', '')
                supabase_user_id = f"{c[:8]}-{c[8:12]}-{c[12:16]}-{c[16:20]}-{c[20:]}"
            else:
                supabase_user_id = raw_username
            
            # Determine plan and duration
            plan_name = sub.plan.name.lower()
            is_yearly = 'yearly' in plan_name
            duration_days = 365 if is_yearly else 30
            
            print(f"🔄 Syncing {sub.user.email} (ID: {supabase_user_id}) to {plan_name}...")
            
            success = supabase_service.admin_update_user_metadata(
                user_id=supabase_user_id,
                plan_name=plan_name,
                duration_days=duration_days
            )
            
            if success:
                print(f"✅ Repaired metadata for {sub.user.email}")
                count += 1
            else:
                print(f"❌ Failed to repair metadata for {sub.user.email}")
        except Exception as e:
            print(f"⚠️ Error repairing {sub.user.email}: {e}")
            
    print(f"🏁 Repair finished. Total synchronized: {count}/{active_subs.count()}")

if __name__ == "__main__":
    repair_all_subscriptions()
