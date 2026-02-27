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

CLOUDFLARE_WORKER_URL = 'https://fixpix-image.bcjqxt9wn8.workers.dev/'
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
        resp = requests.post(
            CLOUDFLARE_WORKER_URL,
            json={'prompt': enhanced_prompt},
            headers={
                'Authorization': f'Bearer {CLOUDFLARE_AUTH_TOKEN}',
                'Content-Type': 'application/json',
            },
            timeout=60,
        )

        if resp.status_code != 200:
            logger.error(f"Cloudflare Worker error: {resp.status_code} {resp.text[:200]}")
            return Response(
                {'error': f'Image generation failed ({resp.status_code})'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        # Convert raw image bytes to base64 data URL
        image_bytes = resp.content
        if not image_bytes:
            return Response(
                {'error': 'No image returned from generation service'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        image_b64 = base64.b64encode(image_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{image_b64}"

        return Response({
            'image': data_url,
            'prompt': prompt,
            'style': style,
            'aspectRatio': aspect_ratio,
        })

    except requests.Timeout:
        return Response(
            {'error': 'Image generation timed out. Please try again.'},
            status=status.HTTP_504_GATEWAY_TIMEOUT
        )
    except Exception as e:
        logger.error(f"Image generation proxy error: {e}")
        return Response(
            {'error': f'Generation failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
