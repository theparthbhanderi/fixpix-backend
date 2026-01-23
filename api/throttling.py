"""
Dynamic Rate Limiting for FixPix

User-tier based rate limiting with Redis caching.
"""

from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class TieredUserRateThrottle(UserRateThrottle):
    """
    Dynamic rate limiting based on user subscription tier.
    
    Free users: 20 requests/minute
    Pro users: 100 requests/minute
    Enterprise: 500 requests/minute
    """
    
    # Default rates by tier
    TIER_RATES = {
        'free': '20/minute',
        'pro': '100/minute',
        'enterprise': '500/minute',
    }
    
    def get_rate(self):
        """Override to return tier-based rate."""
        if self.request.user.is_authenticated:
            # Try to get user's plan from profile
            try:
                plan = getattr(self.request.user, 'profile', None)
                if plan:
                    tier = getattr(plan, 'plan', 'free')
                    return self.TIER_RATES.get(tier, self.TIER_RATES['free'])
            except Exception:
                pass
        
        return self.TIER_RATES['free']
    
    def allow_request(self, request, view):
        """Check if request is allowed based on tier."""
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class ProcessingRateThrottle(UserRateThrottle):
    """
    Separate rate limit for heavy image processing operations.
    
    More restrictive to prevent abuse.
    """
    scope = 'processing'
    
    TIER_RATES = {
        'free': '5/minute',      # Free: 5 processes/minute
        'pro': '30/minute',      # Pro: 30 processes/minute
        'enterprise': '100/minute',
    }
    
    def get_rate(self):
        if self.request.user.is_authenticated:
            try:
                plan = getattr(self.request.user, 'profile', None)
                if plan:
                    tier = getattr(plan, 'plan', 'free')
                    return self.TIER_RATES.get(tier, self.TIER_RATES['free'])
            except Exception:
                pass
        return self.TIER_RATES['free']
    
    def allow_request(self, request, view):
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class UploadRateThrottle(UserRateThrottle):
    """
    Rate limit for file uploads.
    """
    scope = 'uploads'
    
    TIER_RATES = {
        'free': '10/minute',
        'pro': '50/minute',
        'enterprise': '200/minute',
    }
    
    def get_rate(self):
        if self.request.user.is_authenticated:
            try:
                plan = getattr(self.request.user, 'profile', None)
                if plan:
                    tier = getattr(plan, 'plan', 'free')
                    return self.TIER_RATES.get(tier, self.TIER_RATES['free'])
            except Exception:
                pass
        return self.TIER_RATES['free']
    
    def allow_request(self, request, view):
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class StrictAnonThrottle(AnonRateThrottle):
    """
    Strict rate limiting for anonymous users.
    """
    rate = '10/minute'
