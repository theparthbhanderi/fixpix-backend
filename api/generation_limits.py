"""
Generation Limits Service for FixPix

Tracks and enforces daily generation limits per user.
Logs GPU usage for billing and monitoring.

Usage:
    from api.generation_limits import GenerationLimits
    
    limits = GenerationLimits(user)
    if limits.can_generate():
        limits.record_generation(gpu_time_seconds=120)
"""

from django.core.cache import cache
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class GenerationLimits:
    """
    Manages generation rate limits per user.
    
    Uses Django cache for tracking daily counts.
    Supports different limits for free vs premium users.
    """
    
    # Limit configurations
    LIMITS = {
        'free': {
            'daily_generations': 5,
            'max_concurrent': 1,
            'max_prompt_length': 500,
        },
        'premium': {
            'daily_generations': 50,
            'max_concurrent': 3,
            'max_prompt_length': 1000,
        },
    }
    
    # Cache key patterns
    DAILY_COUNT_KEY = 'gen_daily_{user_id}'
    CONCURRENT_KEY = 'gen_concurrent_{user_id}'
    GPU_TIME_KEY = 'gen_gpu_time_{user_id}'
    
    def __init__(self, user):
        self.user = user
        self.user_id = user.id if user else 'anonymous'
        self.tier = self._get_user_tier()
        self.limits = self.LIMITS[self.tier]
    
    def _get_user_tier(self) -> str:
        """
        Determine user's subscription tier.
        
        TODO: Integrate with actual subscription system when available.
        For now, returns 'free' for all users.
        """
        # Check if user has premium flag
        # This can be extended to check Stripe subscription, etc.
        if self.user and hasattr(self.user, 'profile'):
            if hasattr(self.user.profile, 'is_premium') and self.user.profile.is_premium:
                return 'premium'
        return 'free'
    
    def _get_cache_key(self, pattern: str) -> str:
        """Generate cache key for this user."""
        return pattern.format(user_id=self.user_id)
    
    def get_daily_count(self) -> int:
        """Get number of generations today."""
        key = self._get_cache_key(self.DAILY_COUNT_KEY)
        return cache.get(key, 0)
    
    def get_remaining(self) -> int:
        """Get remaining generations for today."""
        return max(0, self.limits['daily_generations'] - self.get_daily_count())
    
    def get_concurrent_count(self) -> int:
        """Get number of currently processing generations."""
        key = self._get_cache_key(self.CONCURRENT_KEY)
        return cache.get(key, 0)
    
    def can_generate(self) -> dict:
        """
        Check if user can start a new generation.
        
        Returns:
            dict with keys:
                - 'allowed': bool
                - 'reason': str (if not allowed)
                - 'remaining': int (generations remaining today)
                - 'daily_limit': int
        """
        daily_count = self.get_daily_count()
        concurrent_count = self.get_concurrent_count()
        daily_limit = self.limits['daily_generations']
        
        result = {
            'allowed': True,
            'reason': None,
            'remaining': max(0, daily_limit - daily_count),
            'daily_limit': daily_limit,
            'tier': self.tier,
        }
        
        # Check daily limit
        if daily_count >= daily_limit:
            result['allowed'] = False
            result['reason'] = f'Daily limit reached ({daily_limit} generations). Resets at midnight UTC.'
            return result
        
        # Check concurrent limit
        if concurrent_count >= self.limits['max_concurrent']:
            result['allowed'] = False
            result['reason'] = f'Maximum concurrent generations reached ({self.limits["max_concurrent"]}). Please wait for current generation to complete.'
            return result
        
        return result
    
    def validate_prompt(self, prompt: str) -> dict:
        """
        Validate generation prompt.
        
        Returns:
            dict with keys:
                - 'valid': bool
                - 'error': str (if not valid)
                - 'sanitized': str (cleaned prompt)
        """
        if not prompt or not prompt.strip():
            return {'valid': False, 'error': 'Prompt cannot be empty'}
        
        prompt = prompt.strip()
        
        # Check length
        max_length = self.limits['max_prompt_length']
        if len(prompt) > max_length:
            return {
                'valid': False,
                'error': f'Prompt too long ({len(prompt)} chars). Maximum is {max_length} characters.'
            }
        
        # Basic sanitization - remove potential injection attempts
        # Remove HTML tags
        import re
        sanitized = re.sub(r'<[^>]+>', '', prompt)
        
        # Remove excessive whitespace
        sanitized = ' '.join(sanitized.split())
        
        return {
            'valid': True,
            'error': None,
            'sanitized': sanitized
        }
    
    def increment_concurrent(self):
        """Mark a new generation as started (concurrent count +1)."""
        key = self._get_cache_key(self.CONCURRENT_KEY)
        try:
            cache.incr(key)
        except ValueError:
            # Key doesn't exist, set it
            cache.set(key, 1, timeout=3600)  # 1 hour timeout for safety
    
    def decrement_concurrent(self):
        """Mark a generation as finished (concurrent count -1)."""
        key = self._get_cache_key(self.CONCURRENT_KEY)
        try:
            val = cache.decr(key)
            if val < 0:
                cache.set(key, 0, timeout=3600)
        except ValueError:
            cache.set(key, 0, timeout=3600)
    
    def record_generation(self, gpu_time_seconds: float = 0, success: bool = True):
        """
        Record a completed generation.
        
        Args:
            gpu_time_seconds: GPU time used for this generation
            success: Whether generation completed successfully
        """
        # Only count successful generations against limit
        # (Failed ones don't consume the user's quota)
        if success:
            # Increment daily count
            daily_key = self._get_cache_key(self.DAILY_COUNT_KEY)
            try:
                cache.incr(daily_key)
            except ValueError:
                # Calculate seconds until midnight UTC
                now = timezone.now()
                midnight = (now + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                timeout = int((midnight - now).total_seconds())
                cache.set(daily_key, 1, timeout=timeout)
        
        # Always decrement concurrent count
        self.decrement_concurrent()
        
        # Log GPU time
        if gpu_time_seconds > 0:
            self._log_gpu_time(gpu_time_seconds)
        
        logger.info(
            f"Generation recorded for user {self.user_id}: "
            f"success={success}, gpu_time={gpu_time_seconds}s, "
            f"remaining={self.get_remaining()}"
        )
    
    def _log_gpu_time(self, seconds: float):
        """Track cumulative GPU time for billing/monitoring."""
        key = self._get_cache_key(self.GPU_TIME_KEY)
        try:
            # Add to existing time
            current = cache.get(key, 0)
            cache.set(key, current + seconds, timeout=86400 * 30)  # 30 day retention
        except Exception as e:
            logger.error(f"Failed to log GPU time: {e}")
    
    def get_status(self) -> dict:
        """Get full status for API response."""
        return {
            'tier': self.tier,
            'daily_limit': self.limits['daily_generations'],
            'used_today': self.get_daily_count(),
            'remaining': self.get_remaining(),
            'concurrent': self.get_concurrent_count(),
            'max_concurrent': self.limits['max_concurrent'],
            'max_prompt_length': self.limits['max_prompt_length'],
        }


def check_generation_allowed(user) -> dict:
    """Convenience function to check if generation is allowed."""
    limits = GenerationLimits(user)
    return limits.can_generate()


def validate_generation_prompt(user, prompt: str) -> dict:
    """Convenience function to validate a prompt."""
    limits = GenerationLimits(user)
    return limits.validate_prompt(prompt)
