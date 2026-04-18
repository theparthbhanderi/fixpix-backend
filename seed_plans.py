import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from subscriptions.models import SubscriptionPlan

def seed_plans():
    plans = [
        {
            'name': 'free',
            'display_name': 'Free',
            'price_inr': 0,
            'razorpay_plan_id': None
        },
        {
            'name': 'pro',
            'display_name': 'Pro',
            'price_inr': 149,
            'razorpay_plan_id': None
        },
        {
            'name': 'elite',
            'display_name': 'Elite',
            'price_inr': 399,
            'razorpay_plan_id': None
        },
        {
            'name': 'pro_yearly',
            'display_name': 'Pro Yearly',
            'price_inr': 1490,
            'razorpay_plan_id': None
        },
        {
            'name': 'elite_yearly',
            'display_name': 'Elite Yearly',
            'price_inr': 3990,
            'razorpay_plan_id': None
        }
    ]

    for plan_data in plans:
        plan, created = SubscriptionPlan.objects.get_or_create(
            name=plan_data['name'],
            defaults={
                'display_name': plan_data['display_name'],
                'price_inr': plan_data['price_inr'],
                'razorpay_plan_id': plan_data['razorpay_plan_id']
            }
        )
        if created:
            print(f"Created plan: {plan.display_name}")
        else:
            print(f"Plan already exists: {plan.display_name}")

if __name__ == '__main__':
    seed_plans()
