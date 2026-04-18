import requests
import logging
from django.conf import settings
import random

logger = logging.getLogger(__name__)

class PixianService:
    """
    Service to interact with Pixian.ai API for high-fidelity background removal.
    Used as a premium fallback or alternate for Photoroom.
    """
    
    BASE_URL = "https://api.pixian.ai/api/v2/remove-background"
    
    def __init__(self):
        raw_keys = getattr(settings, 'PIXIAN_KEYS', '')
        # Format: id:secret,id:secret
        self.key_pairs = [k.strip() for k in raw_keys.split(',') if k.strip() and ':' in k]
        
        if not self.key_pairs:
            logger.warning("PixianService: No API keys found in settings.")

    def remove_background(self, image_bytes):
        """
        Removes background using Pixian.ai API with prioritized key rotation and retry logic.
        Cycles through all available keys until success or exhaustion.
        Returns: Binary image data (PNG) or None.
        """
        if not self.key_pairs:
            logger.error("PixianService: No API keys available for processing.")
            return None

        # Shuffle keys for this request to distribute load
        keys_to_try = self.key_pairs.copy()
        random.shuffle(keys_to_try)

        for key_pair in keys_to_try:
            parts = key_pair.split(':')
            auth = (parts[0], parts[1])
            key_id = parts[0]

            # Pixian expects 'image' field in multipart/form-data
            files = {
                "image": ("image.jpg", image_bytes, "image/jpeg")
            }
            
            # Additional params for better quality
            data = {
                "output.format": "png",
                "output.quality": "high"
            }

            try:
                logger.info(f"PixianService: Attempting BG removal with key {key_id}...")
                response = requests.post(
                    self.BASE_URL, 
                    auth=auth, 
                    files=files, 
                    data=data,
                    timeout=30
                )
                
                if response.status_code == 200:
                    logger.info(f"PixianService: Success with key {key_id}")
                    return response.content
                elif response.status_code in [401, 402, 429]:
                    logger.warning(f"PixianService: Key {key_id} failed with status {response.status_code}. Retrying with next key...")
                    continue
                else:
                    logger.error(f"PixianService API Error (Key: {key_id}): {response.status_code} - {response.text}")
                    # For other errors, we still try the next key just in case it's key-specific
                    continue
                    
            except Exception as e:
                logger.error(f"PixianService Exception with key {key_id}: {str(e)}")
                continue
        
        logger.error("PixianService: All available API keys failed or were exhausted.")
        return None
