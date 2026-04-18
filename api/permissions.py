from rest_framework import permissions
import jwt
import os
from django.conf import settings


class IsAdminUser(permissions.BasePermission):
    """
    Allows access only to admin users (is_staff=True).
    Used for basic staff-level checks.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)


class IsSuperUser(permissions.BasePermission):
    """
    Allows access only to superusers.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class IsAdminJWT(permissions.BasePermission):
    """
    Validates the request carries a valid Admin JWT with role=admin claim.
    
    This is the PRIMARY permission class for all admin panel API views.
    It works alongside AdminJWTMiddleware but provides view-level protection
    as a defense-in-depth measure.
    
    Unlike IsAdminUser which just checks is_staff on the Django user,
    this verifies the actual JWT token has the admin role claim,
    ensuring the user authenticated through the admin login flow.
    """
    message = 'Admin authentication required. Use the admin login portal.'

    def has_permission(self, request, view):
        # If middleware already validated and attached admin_user, trust it
        if hasattr(request, 'admin_user') and request.admin_user:
            return True

        # Fallback: validate JWT manually (defense-in-depth)
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return False

        token = auth_header.split(' ', 1)[1]
        admin_secret = os.environ.get(
            'ADMIN_SECRET_KEY',
            settings.SECRET_KEY + '-admin-panel-secure'
        )

        try:
            payload = jwt.decode(token, admin_secret, algorithms=['HS256'])
            return payload.get('role') == 'admin'
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
            return False
