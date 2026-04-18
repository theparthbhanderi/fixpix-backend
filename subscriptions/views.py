import razorpay
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import SubscriptionPlan, UserSubscription, UsageLog
from .utils import get_user_plan, get_usage_stats

def get_razorpay_client():
    """Helper to initialize Razorpay client with current settings."""
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_order(request):
    """
    Step 1: Create a Razorpay Order for a specific plan.
    """
    plan_id = request.data.get('plan_id')
    if not plan_id:
        return Response({'error': 'Plan ID is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Normalize plan_id to lowercase
        plan = SubscriptionPlan.objects.get(name=plan_id.lower())
        
        # Free plans don't need a payment order
        if plan.price_inr == 0:
            return Response({'error': 'Free plan does not require payment'}, status=status.HTTP_400_BAD_REQUEST)

        client = get_razorpay_client()
        order_data = {
            'amount': plan.price_inr * 100,  # Razorpay amount is in paise
            'currency': 'INR',
            'payment_capture': '1'
        }
        
        razorpay_order = client.order.create(data=order_data)
        
        return Response({
            'order_id': razorpay_order['id'],
            'amount': plan.price_inr,
            'plan_name': plan.display_name,
            'key_id': settings.RAZORPAY_KEY_ID
        })

    except SubscriptionPlan.DoesNotExist:
        return Response({'error': f'Invalid Plan ID: {plan_id}'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_payment(request):
    """
    Step 2: Verify Razorpay Payment Signature and activate subscription.
    """
    razorpay_order_id = request.data.get('razorpay_order_id')
    razorpay_payment_id = request.data.get('razorpay_payment_id')
    razorpay_signature = request.data.get('razorpay_signature')
    plan_id = request.data.get('plan_id')

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, plan_id]):
        return Response({'error': 'Missing required payment details'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        # Initialize client
        client = get_razorpay_client()
        
        # Verify Signature
        client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature
        })
        
        # Use transaction to ensure data integrity
        with transaction.atomic():
            plan = SubscriptionPlan.objects.get(name=plan_id.lower())
            
            # Determine duration
            is_yearly = 'yearly' in plan.name.lower()
            duration_days = 365 if is_yearly else 30
            
            subscription, created = UserSubscription.objects.update_or_create(
                user=request.user,
                defaults={
                    'plan': plan,
                    'started_at': timezone.now(),
                    'expires_at': timezone.now() + timedelta(days=duration_days),
                    'is_active': True,
                    'razorpay_payment_id': razorpay_payment_id
                }
            )

            # ─── SaaS Single Source of Truth Sync ───────────────────────
            # Sync to Supabase Metadata so Node.js and Frontend are aware
            try:
                from api.services.supabase_service import supabase_service
                
                # RECONSTRUCT VALID UUID:
                # request.user.username is 'sb_' + UUID (32 chars)
                raw_username = request.user.username
                supabase_user_id = None
                
                if raw_username.startswith('sb_'):
                    c = raw_username.replace('sb_', '')
                    if len(c) == 32:
                        # Restore 8-4-4-4-12 format
                        supabase_user_id = f"{c[:8]}-{c[8:12]}-{c[12:16]}-{c[16:20]}-{c[20:]}"
                    else:
                        print(f"⚠️ Username format unexpected length: {len(c)}")
                        supabase_user_id = raw_username
                else:
                    supabase_user_id = raw_username 
                
                if supabase_user_id:
                    sync_success = supabase_service.admin_update_user_metadata(
                        user_id=supabase_user_id,
                        plan_name=plan.name.lower(),
                        duration_days=duration_days
                    )
                    if not sync_success:
                        print(f"⚠️ Metadata sync failed for user {supabase_user_id}. Key issue?")
                else:
                    print("⚠️ Could not determine Supabase User ID for sync.")

            except Exception as sync_err:
                 print(f"⚠️ Critical Sync Error (Non-Fatal for Payment): {sync_err}")

        return Response({
            'status': 'success',
            'message': f'Subscription to {plan.display_name} activated successfully!',
            'expires_at': subscription.expires_at,
            'force_refresh': True
        })

    except Exception as e:
        import traceback
        error_msg = f"VERIFICATION ERROR: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return Response({'error': error_msg}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_status(request):
    # Fetch plan object to get display_name
    try:
        plan_name = get_user_plan(request.user)
        plan_obj = SubscriptionPlan.objects.get(name=plan_name)
        display_name = plan_obj.display_name
    except:
        plan_name = 'free'
        display_name = 'Free Tier'

    usage_stats = get_usage_stats(request.user)
    
    return Response({
        'plan': {
            'name': plan_name,
            'display_name': display_name
        },
        'usage': usage_stats
    })
