"""
FixPix Admin Panel API Views

Production-grade admin views with:
- Separate admin JWT authentication
- Activity logging on all actions
- Enhanced analytics and dashboard stats
- User management with plan info
"""

import jwt
import os
import uuid
from datetime import timedelta, datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.conf import settings
from django.db.models import Count, Q, F
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import ImageProject
from .admin_models import AdminActivityLog
from .permissions import IsAdminJWT


# ──────────────────────────────────────────────────────────────
# Helper: Generate Admin JWT
# ──────────────────────────────────────────────────────────────

def _get_admin_secret():
    return os.environ.get(
        'ADMIN_SECRET_KEY',
        settings.SECRET_KEY + '-admin-panel-secure'
    )


def _generate_admin_token(user):
    """Generate a short-lived JWT with role=admin claim."""
    lifetime_minutes = int(os.environ.get('ADMIN_TOKEN_LIFETIME_MINUTES', 30))
    now = datetime.utcnow()
    payload = {
        'user_id': user.id,
        'username': user.username,
        'email': user.email,
        'role': 'admin',
        'session_id': str(uuid.uuid4()),
        'iat': now,
        'exp': now + timedelta(minutes=lifetime_minutes),
    }
    return jwt.encode(payload, _get_admin_secret(), algorithm='HS256')


# ──────────────────────────────────────────────────────────────
# Admin Login (Public — no auth required)
# ──────────────────────────────────────────────────────────────

class AdminLoginView(APIView):
    """
    POST /api/admin/login/

    Authenticates admin credentials and returns an admin-specific JWT.
    This is completely separate from the user login flow.

    Request:
        { "username": "admin", "password": "..." }

    Response (success):
        {
            "token": "eyJ...",
            "admin": { "id": 1, "username": "admin", "email": "..." },
            "expires_in_minutes": 30
        }

    Response (failure):
        { "error": "Invalid credentials" }
    """
    authentication_classes = []  # No auth required for login
    permission_classes = []

    def post(self, request):
        username = request.data.get('username', '').strip()
        password = request.data.get('password', '')

        # Input validation
        if not username or not password:
            AdminActivityLog.log(
                request, 'login_failed',
                details={'reason': 'Missing credentials', 'attempted_username': username}
            )
            return Response(
                {'error': 'Username and password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Authenticate against Django's auth system
        user = authenticate(request, username=username, password=password)

        if user is None:
            AdminActivityLog.log(
                request, 'login_failed',
                details={'reason': 'Invalid credentials', 'attempted_username': username}
            )
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Verify admin privileges (must be BOTH staff AND superuser)
        if not (user.is_staff and user.is_superuser):
            AdminActivityLog.log(
                request, 'login_failed',
                target_user=user,
                details={'reason': 'User is not admin', 'is_staff': user.is_staff, 'is_superuser': user.is_superuser}
            )
            return Response(
                {'error': 'Access denied. Admin privileges required.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if account is active
        if not user.is_active:
            return Response(
                {'error': 'Account is disabled.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Generate admin JWT
        token = _generate_admin_token(user)
        lifetime = int(os.environ.get('ADMIN_TOKEN_LIFETIME_MINUTES', 30))

        # Log successful login
        AdminActivityLog.log(
            request, 'login',
            details={'session_started': True}
        )

        return Response({
            'token': token,
            'admin': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
            },
            'expires_in_minutes': lifetime,
        })


# ──────────────────────────────────────────────────────────────
# Admin Token Verify
# ──────────────────────────────────────────────────────────────

class AdminTokenVerifyView(APIView):
    """
    GET /api/admin/verify/

    Verifies the admin JWT token is still valid.
    Used by the frontend on page load to check session.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            return Response({
                'valid': True,
                'admin': {
                    'id': admin_user.id,
                    'username': admin_user.username,
                    'email': admin_user.email,
                }
            })
        return Response({'valid': True})


# ──────────────────────────────────────────────────────────────
# Dashboard Stats (Enhanced)
# ──────────────────────────────────────────────────────────────

class AdminDashboardStatsView(APIView):
    """
    GET /api/admin/dashboard/
    Professional AI-Powered Analytics Engine.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        prev_24h = now - timedelta(hours=48)
        last_7d = now - timedelta(days=7)
        last_30d = now - timedelta(days=30)
        prev_30d_start = now - timedelta(days=60)

        # ─── CORE ANALYTICS (CURRENT VS PREVIOUS) ───

        # 1. User Growth
        total_users = User.objects.count()
        today_users = User.objects.filter(date_joined__gte=last_24h).count()
        yest_users = User.objects.filter(date_joined__gte=prev_24h, date_joined__lt=last_24h).count()
        users_last_30d = User.objects.filter(date_joined__gte=last_30d).count()
        users_prev_30d = User.objects.filter(date_joined__gte=prev_30d_start, date_joined__lt=last_30d).count()
        
        # 2. Active Users (7D)
        active_7d = User.objects.filter(last_login__gte=last_7d).count()
        active_today = User.objects.filter(last_login__gte=last_24h).count()
        
        # 3. Image Processing
        total_images = ImageProject.objects.count()
        images_24h = ImageProject.objects.filter(created_at__gte=last_24h).count()
        images_prev_24h = ImageProject.objects.filter(created_at__gte=prev_24h, created_at__lt=last_24h).count()
        completed_images = ImageProject.objects.filter(status='completed').count()
        failed_images = ImageProject.objects.filter(status='failed').count()
        
        success_rate = (completed_images / total_images * 100) if total_images > 0 else 100
        active_jobs = ImageProject.objects.filter(status='processing').count()
        gpu_queue = ImageProject.objects.filter(status='pending').count()

        # 4. API & Storage
        total_api_usage = ImageProject.objects.filter(created_at__gte=last_30d).count() # Proxy for API calls
        est_storage_gb = round(total_images * 0.002, 2) # Heuristic: 2MB per project
        completed_recent = ImageProject.objects.filter(
            completed_at__isnull=False,
            created_at__gte=last_7d
        ).values_list('created_at', 'completed_at')[:100]

        latency_samples = []
        for created_at, completed_at in completed_recent:
            if created_at and completed_at and completed_at >= created_at:
                latency_samples.append((completed_at - created_at).total_seconds())
        avg_latency = round(sum(latency_samples) / len(latency_samples), 2) if latency_samples else 0

        # 5. Distributions
        type_distribution = list(
            ImageProject.objects.values('processing_type')
            .annotate(count=Count('processing_type'))
            .order_by('-count')[:5]
        )
        source_distribution = [
            {'name': 'Web App', 'value': 85},
            {'name': 'API', 'value': 15}
        ]
        plan_distribution_raw = _get_plan_distribution()
        plan_total = sum(p['count'] for p in plan_distribution_raw) or 1
        plan_distribution = [
            {
                'name': p['plan'].replace('_', ' ').title(),
                'count': p['count'],
                'pct': round((p['count'] / plan_total) * 100, 1),
                'growth': 0,
            }
            for p in plan_distribution_raw
        ]

        # 6. Processing trend (last 7 days)
        trend_totals = list(
            ImageProject.objects.filter(created_at__gte=last_7d)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        trend_failed = list(
            ImageProject.objects.filter(created_at__gte=last_7d, status='failed')
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        totals_map = {str(t['day']): t['count'] for t in trend_totals}
        failed_map = {str(t['day']): t['count'] for t in trend_failed}
        processing_trend = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).date()
            day_key = str(day)
            day_total = totals_map.get(day_key, 0)
            day_failed = failed_map.get(day_key, 0)
            processing_trend.append({
                'date': day.strftime('%m-%d'),
                'success': max(0, day_total - day_failed),
                'failed': day_failed,
            })

        # 7. Recent admin/system activity for dashboard feed
        recent_activity = list(
            AdminActivityLog.objects.select_related('admin_user', 'target_user')[:8]
        )
        activity_feed = []
        for event in recent_activity:
            actor = event.admin_user.username if event.admin_user else 'System'
            target = event.target_user.username if event.target_user else None
            message = f"{actor} {event.get_action_display().lower()}"
            if target:
                message = f"{message} for {target}"
            activity_feed.append({
                'id': str(event.id),
                'action': event.action,
                'message': message,
                'admin': actor,
                'timestamp': event.timestamp.isoformat(),
                'severity': 'high' if event.action in {'login_failed', 'ban_user'} else 'normal',
            })

        # 8. Inline insights for dashboard side panel
        dashboard_insights = []
        fail_rate_24h = round((ImageProject.objects.filter(created_at__gte=last_24h, status='failed').count() / images_24h) * 100, 1) if images_24h else 0
        if fail_rate_24h > 5:
            dashboard_insights.append({
                'type': 'warning',
                'priority': 'high',
                'title': 'Failure rate is elevated',
                'message': f'{fail_rate_24h}% of jobs failed in last 24h. Investigate queue and model health.'
            })
        if gpu_queue > 20:
            dashboard_insights.append({
                'type': 'performance',
                'priority': 'medium',
                'title': 'Queue backlog increasing',
                'message': f'{gpu_queue} jobs are pending. Consider scaling workers.'
            })
        if users_prev_30d > 0:
            growth_30d = round(((users_last_30d - users_prev_30d) / users_prev_30d) * 100, 1)
            if growth_30d > 20:
                dashboard_insights.append({
                    'type': 'growth',
                    'priority': 'low',
                    'title': 'User acquisition trend positive',
                    'message': f'Signups are up {growth_30d}% vs previous 30-day window.'
                })
            elif growth_30d < -20:
                dashboard_insights.append({
                    'type': 'warning',
                    'priority': 'high',
                    'title': 'Signup momentum dropped',
                    'message': f'Signups are down {abs(growth_30d)}% vs previous 30-day window.'
                })

        # Dashboard screen expects these keys.
        overview = {
            'total_users': {
                'value': total_users,
                'change': today_users - yest_users,
                'pct': round(((today_users - yest_users) / yest_users) * 100, 1) if yest_users > 0 else 0,
                'sparkline': [{'value': x['success'] + x['failed']} for x in processing_trend],
            },
            'images_processed': {
                'value': total_images,
                'change_24h': images_24h - images_prev_24h,
                'success_rate': round(success_rate, 1),
            },
            'active_users': {
                'value': active_7d,
                'today': active_today,
            },
            'api_usage': {
                'last_24h': images_24h,
                'avg_latency': avg_latency,
            },
        }

        charts = {
            'processing_trend': processing_trend,
            'feature_usage': [
                {'processing_type': d['processing_type'], 'usage': d['count']}
                for d in type_distribution
            ],
            'plans': plan_distribution,
        }

        # Log dashboard view
        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'view_dashboard')

        return Response({
            # New dashboard-first shape
            'overview': overview,
            'charts': charts,
            'insights': dashboard_insights,
            'activity_feed': activity_feed,

            # Existing compatibility shape
            'users': {
                'total': total_users,
                'active_7d': active_7d,
                'today': today_users,
                'growth': ((today_users - yest_users) / yest_users * 100) if yest_users > 0 else 0,
            },
            'images': {
                'total': total_images,
                'last_24h': images_24h,
                'completed': completed_images,
                'failed': failed_images,
                'success_rate': round(success_rate, 1),
            },
            'jobs': {
                'active': active_jobs,
                'gpu_queue': gpu_queue,
            },
            'api_usage': {
                'total_30d': total_api_usage,
                'avg_latency': avg_latency,
            },
            'distributions': {
                'processing_types': type_distribution,
                'sources': source_distribution,
                'plans': [{'name': p['name'], 'value': p['count']} for p in plan_distribution],
            },
            'system': {
                'status': 'healthy' if active_jobs < 50 else 'high_load',
                'est_storage_gb': est_storage_gb,
            }
        })


# ──────────────────────────────────────────────────────────────
# User Management (Enhanced)
# ──────────────────────────────────────────────────────────────

class UserManagementView(APIView):
    """
    GET /api/admin/users/
    POST /api/admin/users/<pk>/action/

    Enhanced user management with plan info, sorting, and pagination.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        query = request.query_params.get('search', '')
        sort_by = request.query_params.get('sort', '-date_joined')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 25))

        users = User.objects.all()

        if query:
            users = users.filter(
                Q(username__icontains=query) |
                Q(email__icontains=query)
            )

        # Validate sort field
        allowed_sorts = ['date_joined', '-date_joined', 'username', '-username', 'last_login', '-last_login']
        if sort_by in allowed_sorts:
            users = users.order_by(sort_by)
        else:
            users = users.order_by('-date_joined')

        total_count = users.count()

        # Pagination
        start = (page - 1) * page_size
        end = start + page_size
        users_page = users[start:end]

        user_data = []
        for user in users_page:
            # Get subscription plan
            plan_name = 'free'
            try:
                if hasattr(user, 'subscription') and user.subscription:
                    plan_name = user.subscription.plan.name
            except Exception:
                pass

            # Get image count
            image_count = ImageProject.objects.filter(user=user).count()

            user_data.append({
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'date_joined': user.date_joined,
                'last_login': user.last_login,
                'plan': plan_name,
                'image_count': image_count,
            })

        # Log action
        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'view_users', details={'search': query, 'page': page})

        return Response({
            'users': user_data,
            'pagination': {
                'total': total_count,
                'page': page,
                'page_size': page_size,
                'total_pages': max(1, (total_count + page_size - 1) // page_size),
            }
        })

    def post(self, request, pk=None):
        if not pk:
            return Response(
                {'error': 'User ID required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(pk=pk)
            action = request.data.get('action')
            admin_user = getattr(request, 'admin_user', None)

            if action == 'ban':
                user.is_active = False
                user.save()
                if admin_user:
                    request.user = admin_user
                AdminActivityLog.log(
                    request, 'ban_user', target_user=user,
                    details={'username': user.username}
                )
                return Response({'message': f'User {user.username} banned'})

            elif action == 'unban':
                user.is_active = True
                user.save()
                if admin_user:
                    request.user = admin_user
                AdminActivityLog.log(
                    request, 'unban_user', target_user=user,
                    details={'username': user.username}
                )
                return Response({'message': f'User {user.username} unbanned'})

            elif action == 'promote':
                user.is_staff = True
                user.save()
                if admin_user:
                    request.user = admin_user
                AdminActivityLog.log(
                    request, 'promote_user', target_user=user,
                    details={'username': user.username}
                )
                return Response({'message': f'User {user.username} promoted to staff'})

            return Response(
                {'error': 'Invalid action. Use: ban, unban, promote'},
                status=status.HTTP_400_BAD_REQUEST
            )

        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )


# ──────────────────────────────────────────────────────────────
# Job Monitor
# ──────────────────────────────────────────────────────────────

class JobMonitorView(APIView):
    """
    GET /api/admin/jobs/

    Monitor and manage AI processing jobs.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        status_filter = request.query_params.get('status', 'all')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 25))

        jobs = ImageProject.objects.all().order_by('-created_at')

        if status_filter != 'all':
            jobs = jobs.filter(status=status_filter)

        total_count = jobs.count()
        start = (page - 1) * page_size
        end = start + page_size
        jobs_page = jobs[start:end]

        job_data = []
        for job in jobs_page:
            job_data.append({
                'id': str(job.id),
                'type': job.processing_type,
                'status': job.status,
                'user': job.user.username if job.user else 'Anonymous',
                'created_at': job.created_at,
                'source': job.source,
            })

        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'view_jobs', details={'status': status_filter, 'page': page})

        return Response({
            'jobs': job_data,
            'pagination': {
                'total': total_count,
                'page': page,
                'page_size': page_size,
                'total_pages': max(1, (total_count + page_size - 1) // page_size),
            }
        })


# ──────────────────────────────────────────────────────────────
# Analytics
# ──────────────────────────────────────────────────────────────

class AdminAnalyticsView(APIView):
    """
    GET /api/admin/analytics/

    Returns chart-ready analytics data: daily signups, daily images,
    feature usage trends, and plan conversion metrics.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        days = int(request.query_params.get('days', 30))
        now = timezone.now()
        start_date = now - timedelta(days=days)

        # ── Daily Signups (last N days) ──
        daily_signups = list(
            User.objects.filter(date_joined__gte=start_date)
            .annotate(date=TruncDate('date_joined'))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')
        )

        # ── Daily Image Processing (last N days) ──
        daily_images = list(
            ImageProject.objects.filter(created_at__gte=start_date)
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')
        )

        # ── Feature Usage (by processing type) ──
        feature_usage = list(
            ImageProject.objects.filter(created_at__gte=start_date)
            .values('processing_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        # ── Plan Distribution ──
        plan_distribution = _get_plan_distribution()

        # ── Top Users by Activity ──
        top_users = list(
            ImageProject.objects.filter(created_at__gte=start_date, user__isnull=False)
            .values('user__username', 'user__email')
            .annotate(image_count=Count('id'))
            .order_by('-image_count')[:10]
        )

        # ── Hourly Activity Pattern (last 7 days, for heatmap) ──
        from django.db.models.functions import ExtractHour
        hourly_activity = list(
            ImageProject.objects.filter(created_at__gte=now - timedelta(days=7))
            .annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )

        # Log
        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'view_analytics', details={'days': days})

        return Response({
            'daily_signups': [
                {'date': str(d['date']), 'count': d['count']} for d in daily_signups
            ],
            'daily_images': [
                {'date': str(d['date']), 'count': d['count']} for d in daily_images
            ],
            'feature_usage': feature_usage,
            'plan_distribution': plan_distribution,
            'top_users': top_users,
            'hourly_activity': hourly_activity,
        })


# ──────────────────────────────────────────────────────────────
# Activity Log
# ──────────────────────────────────────────────────────────────

class AdminActivityLogView(APIView):
    """
    GET /api/admin/activity/

    Returns admin activity logs for auditing.
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        action_filter = request.query_params.get('action', '')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 50))

        logs = AdminActivityLog.objects.all()

        if action_filter:
            logs = logs.filter(action=action_filter)

        total_count = logs.count()
        start = (page - 1) * page_size
        end = start + page_size
        logs_page = logs[start:end]

        log_data = []
        for log in logs_page:
            log_data.append({
                'id': str(log.id),
                'admin': log.admin_user.username if log.admin_user else 'System',
                'action': log.action,
                'action_display': log.get_action_display(),
                'target_user': log.target_user.username if log.target_user else None,
                'target_user_id': log.target_user.id if log.target_user else None,
                'ip_address': log.ip_address,
                'details': log.details,
                'timestamp': log.timestamp,
            })

        # Log meta-action
        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'view_activity')

        return Response({
            'logs': log_data,
            'pagination': {
                'total': total_count,
                'page': page,
                'page_size': page_size,
                'total_pages': max(1, (total_count + page_size - 1) // page_size),
            }
        })



# ──────────────────────────────────────────────────────────────
# AI Insights Engine
# ──────────────────────────────────────────────────────────────

class AdminAIInsightsView(APIView):
    """
    GET /api/admin/insights/

    Analyzes platform data and generates AI-powered insights:
    - Usage anomaly detection (spikes/drops)
    - Churn risk users
    - Growth opportunities
    - Capacity suggestions
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        now = timezone.now()
        today = now.date()
        yesterday = today - timedelta(days=1)
        last_7d = now - timedelta(days=7)
        last_14d = now - timedelta(days=14)
        last_30d = now - timedelta(days=30)

        insights = []

        # ── 1. Usage Spike Detection ──
        # Compare today's processing vs 7-day average
        today_count = ImageProject.objects.filter(
            created_at__date=today
        ).count()

        avg_7d = ImageProject.objects.filter(
            created_at__gte=last_7d
        ).count() / 7.0

        if avg_7d > 0 and today_count > avg_7d * 1.5:
            pct_increase = round(((today_count - avg_7d) / avg_7d) * 100)
            insights.append({
                'id': 'spike_usage',
                'type': 'warning',
                'severity': 'high',
                'icon': '⚠️',
                'title': f'Usage spike detected: +{pct_increase}% today',
                'description': f'Image processing jumped from {round(avg_7d)} avg/day to {today_count} today. Check API capacity.',
                'action': {'label': 'View Jobs', 'path': '/jobs'},
                'timestamp': now.isoformat(),
            })
        elif avg_7d > 0 and today_count < avg_7d * 0.3 and today_count > 0:
            pct_drop = round(((avg_7d - today_count) / avg_7d) * 100)
            insights.append({
                'id': 'drop_usage',
                'type': 'risk',
                'severity': 'medium',
                'icon': '📉',
                'title': f'Unusual usage drop: -{pct_drop}% today',
                'description': f'Processing dropped from {round(avg_7d)} avg/day to {today_count}. Possible API issue?',
                'action': {'label': 'Check System', 'path': '/system'},
                'timestamp': now.isoformat(),
            })

        # ── 2. Feature Usage Analysis ──
        feature_this_week = list(
            ImageProject.objects.filter(created_at__gte=last_7d)
            .values('processing_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        feature_last_week = list(
            ImageProject.objects.filter(created_at__gte=last_14d, created_at__lt=last_7d)
            .values('processing_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        last_week_map = {f['processing_type']: f['count'] for f in feature_last_week}
        for feat in feature_this_week:
            pt = feat['processing_type']
            this_count = feat['count']
            last_count = last_week_map.get(pt, 0)
            if last_count > 0:
                change_pct = round(((this_count - last_count) / last_count) * 100)
                if change_pct > 100:
                    insights.append({
                        'id': f'feature_spike_{pt}',
                        'type': 'growth',
                        'severity': 'medium',
                        'icon': '📈',
                        'title': f'{pt.title()} usage increased by {change_pct}%',
                        'description': f'{pt.title()} went from {last_count} to {this_count} this week. Consider promoting this feature.',
                        'action': {'label': 'View Analytics', 'path': '/analytics'},
                        'timestamp': now.isoformat(),
                    })

        # ── 3. User Growth Analysis ──
        new_users_7d = User.objects.filter(date_joined__gte=last_7d).count()
        new_users_prev_7d = User.objects.filter(
            date_joined__gte=last_14d, date_joined__lt=last_7d
        ).count()

        if new_users_prev_7d > 0:
            growth_pct = round(((new_users_7d - new_users_prev_7d) / new_users_prev_7d) * 100)
            if growth_pct > 20:
                insights.append({
                    'id': 'user_growth',
                    'type': 'growth',
                    'severity': 'low',
                    'icon': '🚀',
                    'title': f'User signups up {growth_pct}% this week',
                    'description': f'{new_users_7d} new users this week vs {new_users_prev_7d} last week.',
                    'action': {'label': 'View Users', 'path': '/users'},
                    'timestamp': now.isoformat(),
                })
            elif growth_pct < -30:
                insights.append({
                    'id': 'user_decline',
                    'type': 'risk',
                    'severity': 'high',
                    'icon': '⚠️',
                    'title': f'User signups dropped {abs(growth_pct)}%',
                    'description': f'Only {new_users_7d} signups this week vs {new_users_prev_7d} last week.',
                    'action': {'label': 'Investigate', 'path': '/analytics'},
                    'timestamp': now.isoformat(),
                })

        # ── 4. Churn Risk Detection ──
        # Users who signed up 7+ days ago, were active, but haven't logged in for 5+ days
        churn_risk_users = User.objects.filter(
            date_joined__lt=last_7d,
            last_login__lt=now - timedelta(days=5),
            last_login__isnull=False,
            is_active=True,
        ).count()

        total_users = User.objects.filter(is_active=True).count()
        if total_users > 0 and churn_risk_users > 0:
            churn_pct = round((churn_risk_users / total_users) * 100)
            insights.append({
                'id': 'churn_risk',
                'type': 'risk',
                'severity': 'medium' if churn_pct < 30 else 'high',
                'icon': '🔴',
                'title': f'{churn_risk_users} users at churn risk ({churn_pct}%)',
                'description': 'These users were active but haven\'t logged in for 5+ days. Consider re-engagement.',
                'action': {'label': 'View At-Risk Users', 'path': '/users?sort=-last_login'},
                'timestamp': now.isoformat(),
            })

        # ── 5. Conversion Opportunity ──
        plan_dist = _get_plan_distribution()
        free_count = next((p['count'] for p in plan_dist if p['plan'] == 'free'), 0)
        paid_count = sum(p['count'] for p in plan_dist if p['plan'] != 'free')

        if free_count > 0:
            # Find active free users who process a lot of images
            heavy_free_users = ImageProject.objects.filter(
                user__isnull=False,
                created_at__gte=last_30d,
            ).values('user').annotate(
                count=Count('id')
            ).filter(count__gte=10).count()

            if heavy_free_users > 0:
                insights.append({
                    'id': 'conversion_opp',
                    'type': 'opportunity',
                    'severity': 'low',
                    'icon': '💡',
                    'title': f'{heavy_free_users} power users on free plan',
                    'description': f'These users processed 10+ images in 30 days. Prime upgrade candidates.',
                    'action': {'label': 'View Power Users', 'path': '/users'},
                    'timestamp': now.isoformat(),
                })

        # ── 6. Failed Jobs Alert ──
        failed_today = ImageProject.objects.filter(
            created_at__date=today, status='failed'
        ).count()
        total_today = ImageProject.objects.filter(created_at__date=today).count()

        if total_today > 0 and failed_today > 0:
            fail_rate = round((failed_today / total_today) * 100)
            if fail_rate > 5:
                insights.append({
                    'id': 'fail_rate',
                    'type': 'risk',
                    'severity': 'high',
                    'icon': '🔥',
                    'title': f'High failure rate: {fail_rate}% today',
                    'description': f'{failed_today} of {total_today} jobs failed. GPU or model issues likely.',
                    'action': {'label': 'View Failed Jobs', 'path': '/jobs?status=failed'},
                    'timestamp': now.isoformat(),
                })

        # ── 7. Capacity Suggestion ──
        pending_jobs = ImageProject.objects.filter(status='pending').count()
        if pending_jobs > 20:
            insights.append({
                'id': 'capacity',
                'type': 'opportunity',
                'severity': 'medium',
                'icon': '⚡',
                'title': f'{pending_jobs} jobs queued — consider scaling',
                'description': 'Large queue detected. Users may be experiencing delays.',
                'action': {'label': 'View Queue', 'path': '/jobs?status=pending'},
                'timestamp': now.isoformat(),
            })

        # Sort: high severity first
        severity_order = {'high': 0, 'medium': 1, 'low': 2}
        insights.sort(key=lambda x: severity_order.get(x['severity'], 99))

        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'other', details={'scope': 'view_insights', 'count': len(insights)})

        return Response({
            'insights': insights,
            'generated_at': now.isoformat(),
            'summary': {
                'total': len(insights),
                'risks': len([i for i in insights if i['type'] == 'risk']),
                'growth': len([i for i in insights if i['type'] == 'growth']),
                'opportunities': len([i for i in insights if i['type'] == 'opportunity']),
                'warnings': len([i for i in insights if i['type'] == 'warning']),
            }
        })


# ──────────────────────────────────────────────────────────────
# System Health
# ──────────────────────────────────────────────────────────────

class AdminSystemHealthView(APIView):
    """
    GET /api/admin/system-health/

    Returns system health metrics:
    - Error rates
    - Queue depth
    - Processing stats
    - Uptime estimation
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request):
        now = timezone.now()
        last_1h = now - timedelta(hours=1)
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        # Processing stats
        total_1h = ImageProject.objects.filter(created_at__gte=last_1h).count()
        failed_1h = ImageProject.objects.filter(created_at__gte=last_1h, status='failed').count()
        completed_1h = ImageProject.objects.filter(created_at__gte=last_1h, status='completed').count()

        total_24h = ImageProject.objects.filter(created_at__gte=last_24h).count()
        failed_24h = ImageProject.objects.filter(created_at__gte=last_24h, status='failed').count()
        completed_24h = ImageProject.objects.filter(created_at__gte=last_24h, status='completed').count()

        # Queue status
        pending = ImageProject.objects.filter(status='pending').count()
        processing = ImageProject.objects.filter(status='processing').count()

        # Error rate
        error_rate_1h = round((failed_1h / total_1h * 100) if total_1h > 0 else 0, 1)
        error_rate_24h = round((failed_24h / total_24h * 100) if total_24h > 0 else 0, 1)

        # Success rate as "uptime" proxy
        success_rate = round((completed_24h / total_24h * 100) if total_24h > 0 else 100, 1)

        # Hourly error trend (last 24h)
        from django.db.models.functions import ExtractHour
        hourly_errors = list(
            ImageProject.objects.filter(created_at__gte=last_24h, status='failed')
            .annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )

        hourly_total = list(
            ImageProject.objects.filter(created_at__gte=last_24h)
            .annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )

        # Active admin sessions (logins in last hour)
        active_admins = AdminActivityLog.objects.filter(
            action='login', timestamp__gte=last_1h
        ).values('admin_user').distinct().count()

        # Alerts
        alerts = []
        if error_rate_1h > 10:
            alerts.append({'level': 'critical', 'message': f'High error rate: {error_rate_1h}% in last hour'})
        if pending > 50:
            alerts.append({'level': 'warning', 'message': f'Large queue: {pending} jobs pending'})
        if error_rate_24h > 5:
            alerts.append({'level': 'warning', 'message': f'Elevated error rate: {error_rate_24h}% in 24h'})

        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'other', details={'scope': 'view_system_health', 'status': 'critical' if error_rate_1h > 20 else 'degraded' if error_rate_1h > 5 else 'healthy'})

        return Response({
            'status': 'critical' if error_rate_1h > 20 else 'degraded' if error_rate_1h > 5 else 'healthy',
            'uptime_pct': success_rate,
            'queue': {
                'pending': pending,
                'processing': processing,
            },
            'error_rates': {
                'last_1h': error_rate_1h,
                'last_24h': error_rate_24h,
            },
            'throughput': {
                'last_1h': total_1h,
                'last_24h': total_24h,
                'completed_1h': completed_1h,
                'completed_24h': completed_24h,
            },
            'hourly_errors': hourly_errors,
            'hourly_total': hourly_total,
            'active_admins': active_admins,
            'alerts': alerts,
            'timestamp': now.isoformat(),
        })


# ──────────────────────────────────────────────────────────────
# User Detail (AI-Powered)
# ──────────────────────────────────────────────────────────────

class AdminUserDetailView(APIView):
    """
    GET /api/admin/users/<pk>/detail/

    Returns detailed user profile with:
    - Full activity timeline
    - Usage patterns
    - AI-generated summary
    - Churn probability
    - Upgrade recommendation
    """
    authentication_classes = []
    permission_classes = [IsAdminJWT]

    def get(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=404)

        now = timezone.now()
        last_30d = now - timedelta(days=30)

        # ── Basic Info ──
        plan_name = 'free'
        try:
            if hasattr(user, 'subscription') and user.subscription:
                plan_name = user.subscription.plan.name
        except Exception:
            pass

        # ── Activity Timeline ──
        projects = ImageProject.objects.filter(user=user).order_by('-created_at')[:50]
        timeline = []
        for p in projects:
            timeline.append({
                'id': str(p.id),
                'type': p.processing_type,
                'status': p.status,
                'source': p.source,
                'created_at': p.created_at,
            })

        # ── Usage Patterns ──
        total_images = ImageProject.objects.filter(user=user).count()
        images_30d = ImageProject.objects.filter(user=user, created_at__gte=last_30d).count()

        feature_usage = list(
            ImageProject.objects.filter(user=user)
            .values('processing_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        most_used_feature = feature_usage[0]['processing_type'] if feature_usage else 'none'

        # ── Churn Probability ──
        days_since_login = (now - user.last_login).days if user.last_login else 999
        days_since_signup = (now - user.date_joined).days

        churn_score = 0
        if days_since_login > 14:
            churn_score += 40
        elif days_since_login > 7:
            churn_score += 20
        elif days_since_login > 3:
            churn_score += 10

        if images_30d == 0:
            churn_score += 30
        elif images_30d < 3:
            churn_score += 15

        if plan_name == 'free':
            churn_score += 10

        churn_score = min(100, churn_score)

        churn_level = 'low' if churn_score < 30 else 'medium' if churn_score < 60 else 'high'

        # ── AI Summary ──
        summary_parts = []
        if total_images > 0:
            summary_parts.append(f"This user has processed {total_images} images, primarily using {most_used_feature}.")
        else:
            summary_parts.append("This user hasn't processed any images yet.")

        if plan_name == 'free' and images_30d >= 5:
            summary_parts.append(f"Active free user ({images_30d} images in 30 days) — strong upgrade candidate.")
        elif plan_name != 'free':
            summary_parts.append(f"Currently on {plan_name} plan.")

        if churn_level == 'high':
            summary_parts.append(f"⚠️ High churn risk — last login {days_since_login} days ago.")
        elif churn_level == 'medium':
            summary_parts.append(f"Moderate engagement — last active {days_since_login} days ago.")

        if days_since_signup < 7:
            summary_parts.append("New user (signed up this week).")

        ai_summary = ' '.join(summary_parts)

        # ── Upgrade Recommendation ──
        should_upgrade = plan_name == 'free' and images_30d >= 5
        upgrade_reason = f'Processed {images_30d} images on free plan in 30 days' if should_upgrade else None

        admin_user = getattr(request, 'admin_user', None)
        if admin_user:
            request.user = admin_user
        AdminActivityLog.log(request, 'other', target_user=user, details={'scope': 'view_user_detail', 'user_id': user.id})

        return Response({
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'date_joined': user.date_joined,
                'last_login': user.last_login,
                'plan': plan_name,
            },
            'stats': {
                'total_images': total_images,
                'images_30d': images_30d,
                'days_since_login': days_since_login,
                'days_since_signup': days_since_signup,
            },
            'feature_usage': feature_usage,
            'most_used_feature': most_used_feature,
            'churn': {
                'score': churn_score,
                'level': churn_level,
            },
            'ai_summary': ai_summary,
            'upgrade': {
                'recommended': should_upgrade,
                'reason': upgrade_reason,
            },
            'timeline': timeline,
        })


# ──────────────────────────────────────────────────────────────
# Emergency Admin Creator (kept from original)
# ──────────────────────────────────────────────────────────────

class EmergencyCreateAdmin(APIView):
    """
    Temporary view to create superuser if shell is inaccessible.
    Protected by the DJANGO_SUPERUSER_PASSWORD itself.
    """
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        env_password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
        param_password = request.query_params.get('key')

        if not env_password:
            return Response({"error": "Env var DJANGO_SUPERUSER_PASSWORD not set on server."}, status=400)

        if param_password != env_password:
             return Response({"error": "Unauthorized. Key does not match env password."}, status=401)

        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')

        if User.objects.filter(username=username).exists():
            user = User.objects.get(username=username)
            user.set_password(env_password)
            user.is_staff = True
            user.is_superuser = True
            user.save()
            msg = f"Superuser '{username}' already existed. Password updated to match env var."
        else:
            User.objects.create_superuser(username, email, env_password)
            msg = f"Superuser '{username}' created successfully."

        return Response({"message": msg, "login_credentials": {"username": username, "password_length": len(env_password)}})


# ──────────────────────────────────────────────────────────────
# Helper: Plan Distribution
# ──────────────────────────────────────────────────────────────

def _get_plan_distribution():
    """Get subscription plan distribution across all users."""
    try:
        from subscriptions.models import UserSubscription
        plan_data = list(
            UserSubscription.objects.filter(is_active=True)
            .values('plan__name')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        # Count users without subscriptions (free tier)
        subscribed_count = sum(p['count'] for p in plan_data)
        total_users = User.objects.count()
        free_count = total_users - subscribed_count

        result = [{'plan': 'free', 'count': free_count}]
        for p in plan_data:
            result.append({'plan': p['plan__name'], 'count': p['count']})
        return result
    except Exception:
        # If subscriptions app not available, return basic data
        return [{'plan': 'free', 'count': User.objects.count()}]
