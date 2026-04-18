import os
import random
import requests
import logging
import base64
import numpy as np
import cv2
from django.conf import settings
from .stability_service import StabilityService
from ..ai_engine import AIEngine

logger = logging.getLogger(__name__)

class MultiProviderService:
    """
    Hyper-Resilient AI Generation Service.
    Rotates Stability AI keys (28 keys) and fallbacks to Pollinations AI.
    """

    @staticmethod
    def generate_sticker(prompt, style="cartoon", outline_width=10, outline_color=(255, 255, 255)):
        """
        Generates a sticker with automatic provider fallback.
        1. Stability AI (Primary)
        2. Pollinations AI (Secondary)
        """
        result_bytes = None
        used_engine = "stability"
        
        # 1. ATTEMPT STABILITY AI (Primary)
        try:
            stability = StabilityService()
            # Upgrade: Using Ultra-Resilient Core engine with production-ready prompts
            result_bytes = stability.generate_sticker_core(prompt, style)
            if result_bytes:
                logger.info(f"MultiProvider: Successfully generated sticker via Stability AI.")
            else:
                logger.warning("MultiProvider: Stability AI returned no data (likely all keys empty).")
        except Exception as e:
            logger.error(f"MultiProvider: Stability AI failed: {e}")

        # 2. ATTEMPT POLLINATIONS AI (Fallback - Magic Engine)
        if not result_bytes:
            used_engine = "pollinations"
            logger.info(f"MultiProvider: Switching to Pollinations AI (Magic Engine) for: {prompt}")
            
            seed = random.randint(1, 1000000)
            # Enhanced prompt for better pollinations results
            aug_prompt = f"{style} style sticker, {prompt}, white background, centered, full subject, vector art, high quality, 4k, die-cut"
            url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(aug_prompt)}?width=1024&height=1024&nologo=true&seed={seed}"
            
            try:
                # Backend fetch to avoid CORS and allow post-processing
                response = requests.get(url, timeout=45)
                if response.status_code == 200:
                    result_bytes = response.content
                    logger.info("MultiProvider: Successfully generated sticker via Pollinations AI.")
                else:
                    logger.error(f"MultiProvider: Pollinations AI failed with status {response.status_code}")
            except Exception as e:
                logger.error(f"MultiProvider: Pollinations AI request failed: {e}")

        if not result_bytes:
            raise Exception("All AI generation providers are currently unavailable.")

        # 3. POST-PROCESSING (Background Removal + Outline)
        # This ensures consistent quality regardless of which AI generated the image
        try:
            # Bytes to Numpy
            nparr = np.frombuffer(result_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Remove Background
            no_bg = AIEngine.remove_background(img, return_path=False)
            if no_bg is None:
                raise Exception("Background removal failed")
                
            # Add Outline
            final_sticker = AIEngine.add_sticker_outline(no_bg, outline_width, outline_color)
            
            # Encode to Data URL
            success, buffer = cv2.imencode('.png', final_sticker)
            final_bytes = buffer.tobytes()
            image_b64 = base64.b64encode(final_bytes).decode('utf-8')
            
            return {
                "data_url": f"data:image/png;base64,{image_b64}",
                "engine": used_engine,
                "raw_image": final_sticker,
                "bytes": final_bytes
            }
        except Exception as e:
            logger.error(f"MultiProvider Post-Processing failed: {e}")
            raise e
