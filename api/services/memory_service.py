import requests
import os
import json
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

class MemoryService:
    def __init__(self):
        self.url = os.environ.get('SUPABASE_URL')
        self.key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_ANON_KEY')

    def get_user_memory(self, user_id):
        """Fetch the latest memory state for a user."""
        if not self.url or not self.key:
            return {}

        try:
            response = requests.get(
                f"{self.url}/rest/v1/user_memory",
                params={"user_id": f"eq.{user_id}", "select": "*", "limit": 1},
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "apikey": self.key
                },
                timeout=5
            )
            if response.status_code == 200 and response.json():
                return response.json()[0].get('memory_state', {})
            return {}
        except Exception as e:
            logger.error(f"Failed to fetch user memory: {e}")
            return {}

    def save_user_memory(self, user_id, memory_state):
        """Update user memory state."""
        if not self.url or not self.key:
            return False

        payload = {
            "user_id": user_id,
            "memory_state": memory_state,
            "updated_at": timezone.now().isoformat()
        }

        try:
            response = requests.post(
                f"{self.url}/rest/v1/user_memory",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "apikey": self.key,
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates"
                },
                json=payload,
                timeout=5
            )
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Failed to save user memory: {e}")
            return False

    def record_action(self, user_id, tool, params=None, image_url=None):
        """Log a tool execution and update 'last_action' memory."""
        memory = self.get_user_memory(user_id)
        memory.update({
            "last_tool": tool,
            "last_params": params or {},
            "last_image": image_url or memory.get('last_image'),
            "updated_at": timezone.now().isoformat()
        })
        return self.save_user_memory(user_id, memory)

# Global instance
memory_service = MemoryService()
