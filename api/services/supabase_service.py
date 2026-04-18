import requests
import os
import logging
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

class SupabaseService:
    def __init__(self):
        self.url = os.environ.get('SUPABASE_URL')
        # Admin operations require the SERVICE_ROLE_KEY
        self.key = os.environ.get('SUPABASE_ANON_KEY')
        self.service_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
        
    def get_user(self, token):
        """Verify token and return user data."""
        if not self.url or not self.key:
            logger.error("Supabase credentials missing in environment")
            return None
            
        try:
            response = requests.get(
                f"{self.url}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": self.key
                },
                timeout=5
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Supabase user verify failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Supabase API error: {str(e)}")
            return None

    def admin_update_user_metadata(self, user_id, plan_name, duration_days=30):
        """
        Force-sync subscription plan to Supabase Auth Metadata (SSOT).
        Uses Service Role Key to bypass RLS.
        """
        if not self.url or not self.service_key:
            logger.error("SUPABASE_SERVICE_ROLE_KEY missing. Cannot sync SaaS metadata.")
            return False

        expiry_date = timezone.now() + timezone.timedelta(days=duration_days)
        
        # Namespaced keys per SaaS requirements v3
        payload = {
            "user_metadata": {
                "fixpix_plan": plan_name,
                "fixpix_plan_status": "active",
                "fixpix_expiry": expiry_date.isoformat()
            }
        }

        try:
            # Supabase Admin Auth API: PUT /auth/v1/admin/users/{user_id}
            response = requests.put(
                f"{self.url}/auth/v1/admin/users/{user_id}",
                headers={
                    "Authorization": f"Bearer {self.service_key}",
                    "apikey": self.service_key,
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=8
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Subscribed user {user_id} to {plan_name} in Supabase Metadata")
                return True
            else:
                logger.error(f"❌ Failed to sync metadata: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Supabase Admin API error: {str(e)}")
            return False

    def sync_profile(self, user_id, email, metadata=None):
        """Sync user profile to Supabase 'profiles' table."""
        if not self.url or not self.key:
            return False
            
        metadata = metadata or {}
        profile_data = {
            "id": user_id,
            "email": email,
            "username": metadata.get('username') or email.split('@')[0],
            "updated_at": "now()"
        }
        
        try:
            response = requests.post(
                f"{self.url}/rest/v1/profiles",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "apikey": self.key,
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates"
                },
                json=profile_data,
                timeout=5
            )
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Supabase profile sync error: {str(e)}")
            return False

# Global instance
supabase_service = SupabaseService()
