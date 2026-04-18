import requests
import logging
from django.conf import settings
import io

logger = logging.getLogger(__name__)

class PhotoroomService:
    """
    Service to interact with Photoroom AI API for professional background removal.
    """
    
    BASE_URL = "https://sdk.photoroom.com/v1/segment"
    
    def __init__(self):
        # Support multiple keys for high-volume rotation
        raw_keys = getattr(settings, 'PHOTOROOM_API_KEY', '')
        self.api_keys = [k.strip() for k in raw_keys.split(',') if k.strip()]
        self.is_sandbox = getattr(settings, 'PHOTOROOM_SANDBOX', False)
        
        if not self.api_keys:
            logger.warning("PhotoroomService: No API keys found in settings.")

    def _get_api_key(self):
        """Rotate through available keys (simple random selection for load balancing)"""
        import random
        if not self.api_keys:
            return None
        return random.choice(self.api_keys)

    def remove_background(self, image_bytes):
        """
        Removes the background from an image using Photoroom Segment API.
        Args:
            image_bytes: Binary image data
        Returns:
            Binary image data (PNG with transparency) or None if failed.
        """
        api_key = self._get_api_key()
        if not api_key:
            return None

        headers = {
            "x-api-key": api_key
        }
        
        files = {
            "image_file": ("image.jpg", image_bytes, "image/jpeg")
        }
        
        # In Sandbox mode, Photoroom might require different handling or just adds watermarks
        # But the endpoint usually remains the same or uses a specific header if documented.
        # Based on user info, Sandbox is for testing.
        
        try:
            logger.info(f"PhotoroomService: Calling Segment API (Sandbox={self.is_sandbox})")
            response = requests.post(self.BASE_URL, headers=headers, files=files, timeout=30)
            
            if response.status_code == 200:
                logger.info("PhotoroomService: Background removed successfully.")
                return response.content
            else:
                logger.error(f"PhotoroomService API Error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"PhotoroomService Exception: {str(e)}")
            return None

    def edit_image(self, image_bytes, prompt=None, background_color=None):
        """
        Advanced editing using Photoroom v2 Image Editing API (Future expansion).
        """
        # For now, we focus on background removal as requested.
        pass
