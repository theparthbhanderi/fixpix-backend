import requests
import os
import logging

logger = logging.getLogger(__name__)

def generate_image(prompt):
    API_URL = "https://imageapi.parthbhanderi24.workers.dev"
    API_KEY = os.getenv("CLOUDFLARE_IMAGE_API_KEY", "cbc5a9ed9cd88913941f8d99241d62ec")
    try:
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={"prompt": prompt},
            timeout=60
        )

        if response.status_code != 200:
            return None

        return response.content  # binary image

    except Exception as e:
        print("Image API Error:", e)
        return None


def edit_image(image_bytes, prompt, strength=0.7):
    """
    Edit an image using Cloudflare Img2Img Worker.
    
    Args:
        image_bytes: Binary image data
        prompt: Description of changes
        strength: Transformation strength (0.1 - 1.0)
    """
    logger.info(f"AI STEP: Starting edit_image with prompt: '{prompt[:50]}...' strength: {strength}")
    API_URL = "https://imageedit.kingparthh.workers.dev/"
    API_KEY = os.getenv("CLOUDFLARE_IMAGE_API_KEY", "cbc5a9ed9cd88913941f8d99241d62ec")
    
    try:
        # Separate files and non-file data for multipart/form-data
        files = {
            "image": ("image.png", image_bytes, "image/png")
        }
        data = {
            "prompt": str(prompt),
            "strength": str(strength)
        }
        
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}"
            },
            files=files,
            data=data,
            timeout=60
        )

        if response.status_code != 200:
            print(f"Edit Image API Error: {response.status_code} {response.text}")
            return None

        # Check if response is actually an image or a JSON error
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                error_data = response.json()
                if "error" in error_data:
                    print(f"Cloudflare Worker Error: {error_data.get('error')} - {error_data.get('details')}")
                    return None
            except Exception:
                pass

        return response.content  # binary image response

    except Exception as e:
        print("Edit Image API Exception:", e)
        return None
