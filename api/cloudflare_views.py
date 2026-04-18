"""
Cloudflare Worker Text-to-Image Proxy View

Proxies text-to-image generation requests to the Cloudflare Worker endpoint.
Returns the generated image as a base64 data URL for the frontend.
"""

import base64
import logging

import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

logger = logging.getLogger(__name__)

CLOUDFLARE_WORKER_URL = 'https://imageapi.parthbhanderi24.workers.dev'
CLOUDFLARE_AUTH_TOKEN = 'cbc5a9ed9cd88913941f8d99241d62ec'

# Style prompt prefixes
STYLE_PREFIXES = {
    'realistic': 'A photorealistic image of',
    'cinematic': 'A cinematic film still of',
    'portrait': 'A professional portrait photograph of',
    'anime': 'An anime-style illustration of',
}

VALID_RATIOS = ['1:1', '4:5', '16:9']


class ImageGenRateThrottle(AnonRateThrottle):
    rate = '10/minute'


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ImageGenRateThrottle])
def generate_image_proxy(request):
    """
    Proxy text-to-image generation through the Cloudflare Worker.

    POST /api/generate/text-to-image/
    Body: { "prompt": str, "style": str, "aspectRatio": str }
    Returns: { "image": "data:image/jpeg;base64,..." }
    """
    prompt = (request.data.get('prompt') or '').strip()
    style = (request.data.get('style') or 'realistic').strip().lower()
    aspect_ratio = (request.data.get('aspectRatio') or '1:1').strip()

    if not prompt:
        return Response(
            {'error': 'Please enter a prompt'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(prompt) > 1000:
        return Response(
            {'error': 'Prompt must be under 1000 characters'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if aspect_ratio not in VALID_RATIOS:
        aspect_ratio = '1:1'

    # Build enhanced prompt with style prefix
    style_prefix = STYLE_PREFIXES.get(style, STYLE_PREFIXES['realistic'])
    enhanced_prompt = f"{style_prefix} {prompt}. High quality, detailed."

    if aspect_ratio == '16:9':
        enhanced_prompt += " Wide landscape format."
    elif aspect_ratio == '4:5':
        enhanced_prompt += " Portrait format."

    try:
        from .services.stability_service import StabilityService
        from .services.cloudflare_image import generate_image as cloudflare_gen
        
        stability = StabilityService()
        
        # Step 1: Attempt SD3.5 Large (Premium Quality) with Smart Prompt Engine
        resp_content = stability.generate_image_sd35(prompt, aspect_ratio, style=style)
        mime_type = "image/webp"
        engine = "sd3.5-large"
        
        # Step 2: Fallback to Stability Core
        if not resp_content:
            logger.info("SD3.5 Large failed. Falling back to Stability Core...")
            resp_content = stability.generate_image(prompt, aspect_ratio)
            engine = "stability-core"
        
        # Step 3: Fallback to Cloudflare
        if not resp_content:
            logger.info("Stability Core failed. Falling back to Cloudflare...")
            resp_content = cloudflare_gen(enhanced_prompt)
            mime_type = "image/jpeg"
            engine = "cloudflare"

        if not resp_content:
            return Response(
                {'error': 'Image generation failed across all AI services'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # Convert raw image bytes to base64 data URL
        image_b64 = base64.b64encode(resp_content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{image_b64}"

        return Response({
            'image': data_url,
            'prompt': prompt,
            'style': style,
            'aspectRatio': aspect_ratio,
            'engine': engine
        })

    except requests.Timeout:
        return Response(
            {'error': 'Image generation timed out. Please try again.'},
            status=status.HTTP_504_GATEWAY_TIMEOUT
        )
    except Exception as e:
        logger.error(f"Image generation proxy error: {e}")
        return Response({
            'error': f'Generation failed: {str(e)}',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ImageGenRateThrottle])
def edit_image_proxy(request):
    """
    Proxy image-to-image editing through the Cloudflare Worker.

    POST /api/generate/edit-image/
    Body (Multipart/Form-Data):
        - image: file
        - prompt: string
        - strength: float (0.1 - 1.0)
    Returns: { "image": "data:image/png;base64,..." }
    """
    image_file = request.FILES.get('image')
    prompt = (request.data.get('prompt') or '').strip()
    strength = request.data.get('strength') or '0.7'

    if not image_file:
        return Response(
            {'error': 'Please upload an image to edit'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if not prompt:
        return Response(
            {'error': 'Please provide a prompt describing the changes'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        strength_float = float(strength)
        strength_float = max(0.1, min(1.0, strength_float))
    except (ValueError, TypeError):
        strength_float = 0.7

    try:
        from .services.stability_service import StabilityService
        from .services.cloudflare_image import edit_image as cloudflare_edit
        
        # Read file as bytes
        image_bytes = image_file.read()
        
        # Step 1: Attempt Stability AI (High Quality)
        stability = StabilityService()
        resp_content = stability.edit_image(image_bytes, prompt, strength_float)
        mime_type = "image/webp"
        
        # Step 2: Fallback to Cloudflare if Stability fails/no keys
        if not resp_content:
            logger.info("Stability AI skipped or failed. Falling back to Cloudflare.")
            resp_content = cloudflare_edit(image_bytes, prompt, strength_float)
            mime_type = "image/png"

        if not resp_content:
            return Response(
                {'error': 'Image editing failed across all AI services'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # Convert result to base64 data URL
        image_b64 = base64.b64encode(resp_content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{image_b64}"

        return Response({
            'image': data_url,
            'prompt': prompt,
            'strength': strength_float,
            'engine': 'stability' if mime_type == 'image/webp' else 'cloudflare'
        })

    except Exception as e:
        logger.error(f"Image edit proxy error: {e}")
        return Response(
            {'error': f'Editing failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
