"""
Admin JWT Middleware for FixPix Admin Panel.

Validates that requests to admin API endpoints carry a valid admin JWT
with role=admin claim. Separate from the standard user JWT flow.
"""

import jwt
import time
import os
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth.models import User
from collections import defaultdict
from threading import Lock


class AdminRateLimiter:
    """
    In-memory rate limiter for admin API endpoints.
    Configurable requests per minute per key.
    """
    def __init__(self, max_requests=240, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key, max_requests=None):
        now = time.time()
        active_limit = max_requests if max_requests is not None else self.max_requests
        with self._lock:
            # Clean old entries
            self._requests[key] = [
                t for t in self._requests[key]
                if now - t < self.window_seconds
            ]
            if len(self._requests[key]) >= active_limit:
                return False
            self._requests[key].append(now)
            return True


# Global rate limiter instance
_admin_rate_limiter = AdminRateLimiter(
    max_requests=int(os.environ.get('ADMIN_RATE_LIMIT_REQUESTS', 240)),
    window_seconds=int(os.environ.get('ADMIN_RATE_LIMIT_WINDOW_SECONDS', 60)),
)


class AdminJWTMiddleware:
    """
    Middleware that protects all /api/admin/* endpoints (except /api/admin/login/).

    Validates:
    1. Admin JWT token presence in Authorization header
    2. Token signature using ADMIN_SECRET_KEY
    3. Token expiry
    4. role=admin claim
    5. User exists and is_staff + is_superuser
    6. Rate limiting (30 req/min per IP)
    7. Optional IP allowlist
    """

    # Endpoints that don't need admin JWT (they handle their own auth)
    EXEMPT_PATHS = [
        '/api/admin/login/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response
        self.admin_secret = os.environ.get(
            'ADMIN_SECRET_KEY',
            settings.SECRET_KEY + '-admin-panel-secure'
        )
        # Optional IP allowlist
        allowed_ips_str = os.environ.get('ADMIN_ALLOWED_IPS', '')
        self.allowed_ips = [
            ip.strip() for ip in allowed_ips_str.split(',') if ip.strip()
        ] if allowed_ips_str else []

    def __call__(self, request):
        path = request.path

        # Only intercept admin API paths
        if not path.startswith('/api/admin/'):
            return self.get_response(request)

        # Skip exempt paths (login endpoint)
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return self.get_response(request)

        # --- IP Allowlist Check ---
        if self.allowed_ips:
            client_ip = self._get_client_ip(request)
            if client_ip not in self.allowed_ips:
                return JsonResponse(
                    {'error': 'Access denied. IP not authorized.'},
                    status=403
                )

        # --- JWT Validation ---
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return JsonResponse(
                {'error': 'Admin authentication required.'},
                status=401
            )

        token = auth_header.split(' ', 1)[1]

        try:
            payload = jwt.decode(
                token,
                self.admin_secret,
                algorithms=['HS256']
            )
        except jwt.ExpiredSignatureError:
            return JsonResponse(
                {'error': 'Admin session expired. Please login again.'},
                status=401
            )
        except jwt.InvalidTokenError:
            return JsonResponse(
                {'error': 'Invalid admin token.'},
                status=401
            )

        # Validate role claim
        if payload.get('role') != 'admin':
            return JsonResponse(
                {'error': 'Insufficient privileges. Admin role required.'},
                status=403
            )

        # Validate user still exists and has admin privileges
        user_id = payload.get('user_id')
        try:
            user = User.objects.get(id=user_id, is_staff=True, is_superuser=True)
            # Attach admin user to request for use in views
            request.admin_user = user
        except User.DoesNotExist:
            return JsonResponse(
                {'error': 'Admin user no longer valid.'},
                status=403
            )

        # --- Rate Limiting ---
        # Key by IP + session/user to avoid penalizing all admin traffic behind one IP.
        client_ip = self._get_client_ip(request)
        session_id = str(payload.get('session_id') or '')
        limiter_key = f"{client_ip}:{session_id or user.id}"

        verify_limit = int(os.environ.get('ADMIN_VERIFY_RATE_LIMIT_REQUESTS', 600))
        is_verify_endpoint = path.endswith('/verify/')
        endpoint_limit = verify_limit if is_verify_endpoint else None

        if not _admin_rate_limiter.is_allowed(limiter_key, max_requests=endpoint_limit):
            max_for_message = endpoint_limit if endpoint_limit is not None else _admin_rate_limiter.max_requests
            return JsonResponse(
                {'error': f'Rate limit exceeded. Max {max_for_message} requests per minute.'},
                status=429
            )

        return self.get_response(request)

    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')
