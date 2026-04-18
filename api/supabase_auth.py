import requests
from django.contrib.auth.models import User
from rest_framework import authentication
from rest_framework import exceptions
from django.conf import settings
from django.core.cache import cache
import os
import logging

logger = logging.getLogger(__name__)

class SupabaseAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ')[1]
        
        # 1. Check Cache first (Optimization)
        cache_key = f"sb_token_{token[-20:]}" # Use last 20 chars of token as key
        cached_user_id = cache.get(cache_key)
        if cached_user_id:
            try:
                user = User.objects.get(id=cached_user_id)
                return (user, None)
            except User.DoesNotExist:
                cache.delete(cache_key)

        # 2. Verify via Supabase API
        supabase_url = os.environ.get('SUPABASE_URL')
        supabase_anon_key = os.environ.get('SUPABASE_ANON_KEY')
        
        if not supabase_url:
            logger.error("SUPABASE_URL not configured in backend environment")
            return None

        try:
            response = requests.get(
                f"{supabase_url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": supabase_anon_key
                },
                timeout=5
            )
            
            if response.status_code != 200:
                raise exceptions.AuthenticationFailed('Invalid or expired Supabase token')

            sb_user = response.json()
            sb_id = sb_user.get('id')
            email = sb_user.get('email')
            
            if not sb_id:
                raise exceptions.AuthenticationFailed('User ID not found in Supabase response')

            # 3. Get or create the Django User
            username = f"sb_{sb_id.replace('-', '')}"
            user, created = User.objects.get_or_create(username=username)
            
            if created or user.email != email:
                user.email = email
                metadata = sb_user.get('user_metadata', {})
                user.first_name = metadata.get('first_name', '')
                user.last_name = metadata.get('last_name', '')
                user.save()

            # 4. Cache the result for 5 minutes
            cache.set(cache_key, user.id, 300)

            return (user, None)

        except requests.exceptions.RequestException as e:
            logger.error(f"Supabase connection error: {str(e)}")
            raise exceptions.AuthenticationFailed('Could not connect to Supabase for authentication')
        except Exception as e:
            logger.error(f"Supabase auth error: {str(e)}")
            raise exceptions.AuthenticationFailed(f'Supabase authentication error: {str(e)}')

    def authenticate_header(self, request):
        return 'Bearer'
