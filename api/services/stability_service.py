import os
import time
import requests
import logging
import random
from django.conf import settings
from .prompt_engine import (
    build_prompt, build_text2img_prompt, build_img2img_prompt,
    build_colorize_prompt, build_upscale_prompt, build_face_prompt,
    build_inpaint_prompt, NEGATIVE_PROMPT, QUALITY_BOOST
)

logger = logging.getLogger(__name__)

class StabilityService:
    """
    Service for Stability AI Generative Image APIs.
    Supports multi-key rotation to pool credits across several accounts.

    Endpoints:
    - SD3.5 Large  (text-to-image, primary)
    - Core         (text-to-image, fallback)
    - SD3 Medium   (image-to-image edit)
    - Conservative Upscaler (synchronous 4K upscale)
    - Creative Upscaler     (async, legacy fallback)
    - Remove Background
    - Colorize via Structure Control
    - Inpaint
    """

    BASE_URL = "https://api.stability.ai/v2beta/stable-image"

    def __init__(self):
        # Support both literal lists and comma-separated strings from .env
        raw_keys = getattr(settings, 'STABILITY_API_KEYS', '')
        if isinstance(raw_keys, str):
            self.api_keys = [k.strip() for k in raw_keys.split(',') if k.strip()]
        else:
            self.api_keys = [k.strip() for k in raw_keys if isinstance(k, str) and k.strip()]
            
        self.exhausted_keys = set()
        if not self.api_keys:
            logger.warning("StabilityService: No API keys configured in STABILITY_API_KEYS.")

    def _get_headers(self, key):
        return {
            "Authorization": f"Bearer {key}",
            "Accept": "image/*"
        }

    def _call_with_rotation(self, method, url, **kwargs):
        """
        Wrapper to handle key rotation and retries for payment/rate limit errors.
        """
        available_keys = [k for k in self.api_keys if k not in self.exhausted_keys]
        if not available_keys:
            if not self.api_keys:
                return None
            logger.warning("StabilityService: All API keys exhausted. Skipping.")
            return None

        random.shuffle(available_keys)

        for key in available_keys:
            headers = kwargs.get('headers', {})
            headers['Authorization'] = f"Bearer {key}"
            kwargs['headers'] = headers

            try:
                logger.info(f"Stability AI: Calling {url} with key starting '{key[:8]}...'")
                response = requests.request(method, url, **kwargs)

                if response.status_code == 200:
                    return response
                elif response.status_code in [402, 429]:
                    logger.warning(f"Stability AI: Key exhausted or rate-limited ({response.status_code}). Trying next key...")
                    self.exhausted_keys.add(key)
                    continue
                else:
                    logger.error(f"Stability AI Error ({response.status_code}): {response.text[:200]}")
                    return response
            except Exception as e:
                logger.error(f"Stability AI Request Exception: {e}")
                continue

        return None

    # ─────────────────────────────────────────────────────
    # TEXT-TO-IMAGE
    # ─────────────────────────────────────────────────────

    def generate_image_sd35(self, prompt, aspect_ratio="1:1", negative_prompt="", style=None):
        """
        SD3.5 Large — Primary text-to-image model.
        Endpoint: /generate/sd3
        Auto-enhanced with Smart Prompt Engine.
        """
        url = f"{self.BASE_URL}/generate/sd3"
        
        # 🧠 Smart Prompt Engine
        engine_result = build_text2img_prompt(prompt, style)
        enhanced_prompt = engine_result["prompt"]
        final_negative = negative_prompt or engine_result["negative_prompt"]
        
        data = {
            "prompt": enhanced_prompt,
            "negative_prompt": final_negative,
            "model": "sd3.5-large",
            "aspect_ratio": aspect_ratio,
            "output_format": "webp",
        }

        response = self._call_with_rotation(
            "POST", url,
            files={"none": ""},
            data=data,
            timeout=90,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    def generate_image(self, prompt, aspect_ratio="1:1"):
        """
        Stability Core Generation — Fallback text-to-image model.
        Endpoint: /generate/core
        Auto-enhanced with Smart Prompt Engine.
        """
        url = f"{self.BASE_URL}/generate/core"
        
        # 🧠 Smart Prompt Engine
        engine_result = build_text2img_prompt(prompt)
        
        data = {
            "prompt": engine_result["prompt"],
            "negative_prompt": engine_result["negative_prompt"],
            "aspect_ratio": aspect_ratio,
            "output_format": "webp"
        }

        response = self._call_with_rotation(
            "POST", url,
            files={"none": ""},
            data=data,
            timeout=60,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    def generate_sticker_image(self, prompt, style="cartoon"):
        """
        Specialized Sticker Generation using SD3.5 Large.
        Uses a hardcoded 'Sticker Guard' template for perfect die-cut results.
        """
        # 🧠 Sticker Guard Prompt Template
        sticker_prompt = f"die-cut sticker, {prompt}, {style} style, high quality illustration, white border, centered subject, plain white background"
        negative_prompt = "complex background, photorealistic, blurry, shadow, cut off, cropped, text, low quality"
        
        return self.generate_image_sd35(
            prompt=sticker_prompt,
            aspect_ratio="1:1",
            negative_prompt=negative_prompt
        )

    def generate_sticker_core(self, prompt, style="cartoon"):
        """
        Ultra-resilient Sticker Generation using Stability Core API.
        Uses the optimized 'Production-Ready' prompt template.
        """
        # 🧠 Optimized Multi-Layer Prompt
        sticker_prompt = f"die-cut sticker, {prompt}, {style} style, white outline, centered, vector style, no background"
        
        url = f"{self.BASE_URL}/generate/core"
        data = {
            "prompt": sticker_prompt,
            "negative_prompt": "shadow, complex background, blurry, low resolution, text, signature, watermark",
            "aspect_ratio": "1:1",
            "output_format": "png"
        }

        response = self._call_with_rotation(
            "POST", url,
            files={"none": ""},
            data=data,
            timeout=60,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    # ─────────────────────────────────────────────────────
    # IMAGE-TO-IMAGE
    # ─────────────────────────────────────────────────────

    def edit_image(self, image_bytes, prompt, strength=0.7):
        """Stability Image-to-Image (Edit) via SD3 Medium. Auto-enhanced."""
        url = f"{self.BASE_URL}/generate/sd3"
        files = {"image": ("image.png", image_bytes, "image/png")}
        
        # 🧠 Smart Prompt Engine
        engine_result = build_img2img_prompt(prompt)
        
        data = {
            "prompt": engine_result["prompt"],
            "negative_prompt": engine_result["negative_prompt"],
            "mode": "image-to-image",
            "strength": strength,
            "model": "sd3-medium",
            "output_format": "webp"
        }

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    # ─────────────────────────────────────────────────────
    # UPSCALE
    # ─────────────────────────────────────────────────────

    def conservative_upscale(self, image_bytes, prompt=""):
        """
        Conservative Upscaler — Synchronous 4K upscale.
        Auto-enhanced with Smart Prompt Engine.
        """
        url = f"{self.BASE_URL}/upscale/conservative"
        files = {"image": ("image.png", image_bytes, "image/png")}
        
        # 🧠 Smart Prompt Engine
        engine_result = build_upscale_prompt(prompt)
        
        data = {
            "prompt": engine_result["prompt"],
            "output_format": "webp"
        }

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=30,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    def upscale_image(self, image_bytes, prompt="High quality, detailed, 4k", creativity=0.3):
        """
        Creative Upscale — Async fallback if Conservative fails.
        Endpoint: /upscale/creative
        """
        url = f"{self.BASE_URL}/upscale/creative"
        files = {"image": ("image.png", image_bytes, "image/png")}
        data = {"prompt": prompt, "creativity": creativity, "output_format": "webp"}

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "application/json"}
        )
        if response and response.status_code == 200:
            gen_id = response.json().get("id")
            auth_header = response.request.headers.get('Authorization', '')
            key = auth_header.replace('Bearer ', '')
            return self._poll_result(gen_id, key)
        return None

    # ─────────────────────────────────────────────────────
    # REMOVE BACKGROUND
    # ─────────────────────────────────────────────────────

    def remove_background(self, image_bytes):
        """
        Remove background from image.
        Endpoint: /edit/remove-background
        Returns PNG image bytes with transparent background.
        """
        url = f"{self.BASE_URL}/edit/remove-background"
        files = {"image": ("image.png", image_bytes, "image/png")}
        data = {"output_format": "png"}

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    def change_background(self, image_bytes, prompt="a natural setting"):
        """
        Change background of image using Search and Replace.
        Searches for 'background' and replaces with user prompt.
        """
        url = f"{self.BASE_URL}/edit/search-and-replace"
        files = {"image": ("image.png", image_bytes, "image/png")}
        data = {
            "prompt": prompt,
            "search_prompt": "background",
            "output_format": "webp"
        }

        # Uses async polling because search-and-replace is a Generation API returning an ID
        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "application/json"}
        )
        if response and response.status_code == 200:
            gen_id = response.json().get("id")
            auth_header = response.request.headers.get('Authorization', '')
            key = auth_header.replace('Bearer ', '')
            return self._poll_result(gen_id, key, result_path="edit")
        return None

    # ─────────────────────────────────────────────────────
    # COLORIZE (via Structure Control)
    # ─────────────────────────────────────────────────────

    def colorize_image(self, image_bytes, prompt=""):
        """
        Colorize B&W images using Structure Control.
        Auto-enhanced with Smart Prompt Engine.
        """
        url = f"{self.BASE_URL}/control/structure"
        files = {"image": ("image.png", image_bytes, "image/png")}
        
        # 🧠 Smart Prompt Engine
        engine_result = build_colorize_prompt(prompt)
        
        data = {
            "prompt": engine_result["prompt"],
            "control_strength": 1.0,
            "output_format": "webp"
        }

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    def style_transfer(
        self, 
        image_bytes, 
        style_image_bytes=None, 
        prompt="", 
        negative_prompt="", 
        style_strength=0.5, 
        composition_fidelity=0.5, 
        change_strength=0.5, 
        seed=0, 
        output_format="jpeg"
    ):
        """
        Style Transfer — Applies stylistic elements (from prompt or style_image) 
        to a base image structure.
        
        Note: If style_image is provided, we use Structure Control with style-enhanced prompt.
        """
        url = f"{self.BASE_URL}/control/structure"
        files = {"image": ("image.png", image_bytes, "image/png")}
        
        # 🧠 Smart Prompt Engine augmentation
        engine_result = build_img2img_prompt(prompt)
        final_prompt = engine_result["prompt"]
        final_negative = negative_prompt or engine_result["negative_prompt"]

        data = {
            "prompt": final_prompt,
            "negative_prompt": final_negative,
            "control_strength": composition_fidelity,  # mapping fidelity to control
            "seed": seed,
            "output_format": output_format
        }

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=90,
            headers={"Accept": "image/*"}
        )
        if response and response.status_code == 200:
            return response.content
        return None

    # ─────────────────────────────────────────────────────
    # INPAINT
    # ─────────────────────────────────────────────────────

    def inpaint_image(self, image_bytes, mask_bytes, prompt=""):
        """Stability Inpaint API."""
        url = f"{self.BASE_URL}/edit/inpaint"
        files = {
            "image": ("image.png", image_bytes, "image/png"),
            "mask": ("mask.png", mask_bytes, "image/png"),
        }
        data = {"prompt": prompt, "output_format": "webp"}

        response = self._call_with_rotation(
            "POST", url, files=files, data=data, timeout=60,
            headers={"Accept": "application/json"}
        )
        if response and response.status_code == 200:
            gen_id = response.json().get("id")
            auth_header = response.request.headers.get('Authorization', '')
            key = auth_header.replace('Bearer ', '')
            return self._poll_result(gen_id, key, result_path="edit")
        return None

    # ─────────────────────────────────────────────────────
    # POLLING (for async endpoints)
    # ─────────────────────────────────────────────────────

    def _poll_result(self, generation_id, key, result_path="upscale", max_attempts=30, interval=5):
        """Poll for an asynchronous generation result."""
        url = f"https://api.stability.ai/v2beta/stable-image/{result_path}/result/{generation_id}"

        for i in range(max_attempts):
            try:
                logger.info(f"Stability AI: Polling {result_path} result {generation_id} (Attempt {i+1})...")
                response = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Accept": "image/*"},
                    timeout=30
                )

                if response.status_code == 200:
                    logger.info("Stability AI: Generation complete.")
                    return response.content
                elif response.status_code == 202:
                    time.sleep(interval)
                else:
                    logger.error(f"Stability AI Poll Error ({response.status_code}): {response.text}")
                    # Fallback switch if wrong path
                    if response.status_code == 404:
                        alt_path = "edit" if result_path == "upscale" else "upscale"
                        url = f"https://api.stability.ai/v2beta/stable-image/{alt_path}/result/{generation_id}"
                        result_path = alt_path
                        continue
                    return None
            except Exception as e:
                logger.error(f"Stability AI Poll Exception: {e}")
                return None
        return None
