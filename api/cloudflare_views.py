"""
Cloudflare Worker Text-to-Image Proxy View

Proxies text-to-image generation requests to the Cloudflare Worker endpoint.
Returns the generated image as a base64 data URL for the frontend.
"""

import base64
import logging
import time

import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from subscriptions.plan_enforcement import check_user_plan_for_request, record_successful_usage
from .utils.watermark import apply_watermark

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

PLAN_OUTPUT_PROFILES = {
    "FREE": {
        "resolution_px": 512,
        "steps": 10,
        "compression": 70,
        "queue": "low",
        "delay_ms": 3000,
        "preview_only": True,
    },
    "PRO": {
        "resolution_px": 1024,
        "steps": 25,
        "compression": 90,
        "queue": "high",
        "delay_ms": 0,
        "preview_only": False,
    },
    "ELITE": {
        "resolution_px": 2048,
        "steps": 50,
        "compression": 100,
        "queue": "priority",
        "delay_ms": 0,
        "preview_only": False,
    },
}


class ImageGenRateThrottle(AnonRateThrottle):
    rate = '10/minute'


def _add_watermark_to_bytes(image_bytes, output_ext=".png", text="FixPix Free"):
    try:
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return image_bytes

        watermarked = apply_watermark(img, text=text, reinforce=True)
        ok, buffer = cv2.imencode(output_ext, watermarked)
        if not ok:
            return image_bytes
        return buffer.tobytes()
    except Exception:
        return image_bytes


def _encode_image_bytes(img, output_ext, compression_quality):
    import cv2

    ext = output_ext.lower()
    if ext in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, int(compression_quality)]
    elif ext == ".webp":
        params = [cv2.IMWRITE_WEBP_QUALITY, int(compression_quality)]
    else:
        # PNG compression 0 (best quality) -> 9 (smallest)
        png_level = max(0, min(9, int((100 - compression_quality) / 12)))
        params = [cv2.IMWRITE_PNG_COMPRESSION, png_level]

    ok, encoded = cv2.imencode(output_ext, img, params)
    if not ok:
        return None
    return encoded.tobytes()


def _resize_to_max_dimension(img, max_dim):
    import cv2

    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_dim:
        return img
    scale = float(max_dim) / float(longest)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _build_plan_variants(image_bytes, mime_type, plan_tier):
    import cv2
    import numpy as np

    profile = PLAN_OUTPUT_PROFILES.get(plan_tier, PLAN_OUTPUT_PROFILES["FREE"])
    output_ext = _extension_from_mime(mime_type)

    nparr = np.frombuffer(image_bytes, np.uint8)
    decoded = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        return {
            "processed_bytes": image_bytes,
            "preview_bytes": image_bytes,
            "demo_bytes": image_bytes,
            "width": 0,
            "height": 0,
            "profile": profile,
        }

    processed = decoded
    if plan_tier in ("FREE", "PRO"):
        processed = _resize_to_max_dimension(processed, profile["resolution_px"])

    if plan_tier == "FREE":
        # Make FREE visibly softer while keeping it usable.
        processed = cv2.GaussianBlur(processed, (0, 0), sigmaX=1.1, sigmaY=1.1)

    processed_bytes = _encode_image_bytes(processed, output_ext, profile["compression"]) or image_bytes

    preview_img = processed
    demo_img = processed
    if plan_tier == "FREE":
        preview_img = cv2.GaussianBlur(processed, (0, 0), sigmaX=3.0, sigmaY=3.0)
        # Demo panel image: sharpened from FREE output to signal upgrade potential.
        gaussian = cv2.GaussianBlur(processed, (0, 0), 1.0)
        demo_img = cv2.addWeighted(processed, 1.45, gaussian, -0.45, 0)

    preview_bytes = _encode_image_bytes(preview_img, output_ext, min(85, profile["compression"] + 5)) or processed_bytes
    demo_bytes = _encode_image_bytes(demo_img, output_ext, 92) or processed_bytes

    h, w = processed.shape[:2]
    return {
        "processed_bytes": processed_bytes,
        "preview_bytes": preview_bytes,
        "demo_bytes": demo_bytes,
        "width": int(w),
        "height": int(h),
        "profile": profile,
    }


def _extension_from_mime(mime_type):
    if mime_type.endswith("webp"):
        return ".webp"
    if mime_type.endswith("jpeg"):
        return ".jpg"
    return ".png"


def _apply_priority_delay(priority):
    # Free users get intentionally slower processing while paid tiers are prioritized.
    if priority == "low":
        time.sleep(3)
    elif priority == "faster":
        time.sleep(0.5)


def _select_edit_model(plan_tier):
    if plan_tier == "ELITE":
        return "best-quality-model"
    if plan_tier == "PRO":
        return "balanced-model"
    return "cheap-fast-model"


def _quality_for_plan(plan_tier):
    if plan_tier == "ELITE":
        return "high"
    if plan_tier == "PRO":
        return "medium"
    return "low"


def _build_plan_profile_payload(plan_tier):
    profile = PLAN_OUTPUT_PROFILES.get(plan_tier, PLAN_OUTPUT_PROFILES["FREE"])
    return {
        "resolution": "high" if plan_tier == "ELITE" else "medium" if plan_tier == "PRO" else "low",
        "resolutionPx": profile["resolution_px"],
        "steps": profile["steps"],
        "compression": profile["compression"],
        "queue": profile["queue"],
        "delayMs": profile["delay_ms"],
        "previewOnly": profile["preview_only"],
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
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

    plan_check = check_user_plan_for_request(request, feature_key="text_to_image", consume=False)
    if not plan_check.get("allowed"):
        return Response(plan_check, status=plan_check.get("status_code", status.HTTP_403_FORBIDDEN))

    plan_tier = plan_check["plan"]
    plan_config = plan_check["plan_config"]
    if not plan_config:
        raise RuntimeError("Plan config missing - do not fallback silently")

    request.plan = plan_tier
    quality = _quality_for_plan(plan_tier)
    profile_payload = _build_plan_profile_payload(plan_tier)
    apply_watermark = plan_tier == "FREE"
    _apply_priority_delay(plan_check.get("priority", "low"))
    logger.info("USER PLAN: %s", request.plan)

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
        
        resp_content = None
        mime_type = "image/webp"
        model = _select_edit_model(plan_tier)

        # FREE: Stability Core. PRO/ELITE: SD3.5 route.
        if plan_tier in ("PRO", "ELITE"):
            resp_content = stability.generate_image_sd35(prompt, aspect_ratio, style=style)
            model = "best-quality-model" if plan_tier == "ELITE" else "balanced-model"

        # Fallback/primary path.
        if not resp_content:
            logger.info("Using Stability Core text-to-image path.")
            resp_content = stability.generate_image(prompt, aspect_ratio)
            model = "cheap-fast-model"
        
        # Step 3: Fallback to Cloudflare
        if not resp_content:
            logger.info("Stability Core failed. Falling back to Cloudflare...")
            resp_content = cloudflare_gen(enhanced_prompt)
            mime_type = "image/jpeg"
            model = _select_edit_model(plan_tier)

        if not resp_content:
            return Response(
                {'error': 'Image generation failed across all AI services'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        variants = _build_plan_variants(resp_content, mime_type, plan_tier)
        resp_content = variants["processed_bytes"]

        if apply_watermark:
            ext = _extension_from_mime(mime_type)
            resp_content = _add_watermark_to_bytes(resp_content, ext, text="FixPix Free")
            preview_bytes = _add_watermark_to_bytes(variants["preview_bytes"], ext, text="FixPix Free")
            demo_bytes = _add_watermark_to_bytes(variants["demo_bytes"], ext, text="FixPix Pro Preview")
        else:
            preview_bytes = variants["preview_bytes"]
            demo_bytes = variants["demo_bytes"]

        logger.info("MODEL USED: %s", model)

        # Convert raw image bytes to base64 data URL
        image_b64 = base64.b64encode(resp_content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{image_b64}"

        preview_b64 = base64.b64encode(preview_bytes).decode('utf-8')
        preview_data_url = f"data:{mime_type};base64,{preview_b64}"

        demo_b64 = base64.b64encode(demo_bytes).decode('utf-8')
        demo_data_url = f"data:{mime_type};base64,{demo_b64}"

        image_to_return = preview_data_url if plan_tier == "FREE" else data_url

        response_payload = {
            'image': image_to_return,
            'prompt': prompt,
            'style': style,
            'aspectRatio': aspect_ratio,
            'engine': model,
            'plan': plan_tier,
            'quality': quality,
            'watermark': apply_watermark,
            'priority': plan_check.get("priority", "low"),
            'planProfile': profile_payload,
            'previewOnly': plan_tier == "FREE",
            'previewImage': preview_data_url,
            'comparisonDemoImage': demo_data_url,
            'upgradeMessage': "Unlock HD output, faster queue, and watermark removal with Pro",
            'dimensions': {
                'width': variants["width"],
                'height': variants["height"],
            },
        }
        record_successful_usage(request, feature_key="text_to_image")
        return Response(response_payload)

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
@permission_classes([IsAuthenticated])
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

    plan_check = check_user_plan_for_request(request, feature_key="edit", consume=False)
    if not plan_check.get("allowed"):
        return Response(plan_check, status=plan_check.get("status_code", status.HTTP_403_FORBIDDEN))

    plan_tier = plan_check["plan"]
    plan_config = plan_check["plan_config"]
    if not plan_config:
        raise RuntimeError("Plan config missing - do not fallback silently")

    request.plan = plan_tier
    quality = _quality_for_plan(plan_tier)
    profile_payload = _build_plan_profile_payload(plan_tier)
    apply_watermark = plan_tier == "FREE"
    priority = plan_check.get("priority", "low")
    _apply_priority_delay(priority)
    logger.info("USER PLAN: %s", request.plan)

    if plan_tier == "FREE":
        # Keep edits lighter for FREE users and force basic provider path.
        strength_float = min(strength_float, 0.65)

    try:
        from .services.stability_service import StabilityService
        from .services.cloudflare_image import edit_image as cloudflare_edit
        
        # Read file as bytes
        image_bytes = image_file.read()
        
        stability = StabilityService()
        resp_content = None
        mime_type = "image/png"
        model = _select_edit_model(plan_tier)

        # Model differentiation:
        # FREE: cheap-fast-model (Cloudflare)
        # PRO: balanced-model (Stability)
        # ELITE: best-quality-model (Stability + upscale finishing)
        if plan_tier in ("PRO", "ELITE"):
            resp_content = stability.edit_image(image_bytes, prompt, strength_float)
            mime_type = "image/webp"
            model = "best-quality-model" if plan_tier == "ELITE" else "balanced-model"

            if plan_tier == "ELITE" and resp_content:
                # Elite gets premium finishing pass for visibly better output.
                elite_upscaled = stability.conservative_upscale(resp_content, prompt="maximum detail, premium quality")
                if elite_upscaled:
                    resp_content = elite_upscaled
                    mime_type = "image/webp"

        # Basic/free and fallback path.
        if not resp_content:
            logger.info("Using Cloudflare edit path (basic tier or fallback).")
            resp_content = cloudflare_edit(image_bytes, prompt, strength_float)
            mime_type = "image/png"
            model = "cheap-fast-model"

        if not resp_content:
            return Response(
                {'error': 'Image editing failed across all AI services'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        variants = _build_plan_variants(resp_content, mime_type, plan_tier)
        resp_content = variants["processed_bytes"]

        if apply_watermark:
            ext = _extension_from_mime(mime_type)
            resp_content = _add_watermark_to_bytes(resp_content, ext, text="FixPix Free")
            preview_bytes = _add_watermark_to_bytes(variants["preview_bytes"], ext, text="FixPix Free")
            demo_bytes = _add_watermark_to_bytes(variants["demo_bytes"], ext, text="FixPix Pro Preview")
        else:
            preview_bytes = variants["preview_bytes"]
            demo_bytes = variants["demo_bytes"]

        logger.info("MODEL USED: %s", model)

        # Convert result to base64 data URL
        image_b64 = base64.b64encode(resp_content).decode('utf-8')
        data_url = f"data:{mime_type};base64,{image_b64}"

        preview_b64 = base64.b64encode(preview_bytes).decode('utf-8')
        preview_data_url = f"data:{mime_type};base64,{preview_b64}"

        demo_b64 = base64.b64encode(demo_bytes).decode('utf-8')
        demo_data_url = f"data:{mime_type};base64,{demo_b64}"

        image_to_return = preview_data_url if plan_tier == "FREE" else data_url

        response_payload = {
            'image': image_to_return,
            'prompt': prompt,
            'strength': strength_float,
            'engine': model,
            'plan': plan_tier,
            'quality': quality,
            'watermark': apply_watermark,
            'priority': priority,
            'planProfile': profile_payload,
            'previewOnly': plan_tier == "FREE",
            'previewImage': preview_data_url,
            'comparisonDemoImage': demo_data_url,
            'upgradeMessage': "Upgrade to Pro for unblurred edits and faster processing",
            'dimensions': {
                'width': variants["width"],
                'height': variants["height"],
            },
        }
        record_successful_usage(request, feature_key="edit")
        return Response(response_payload)

    except Exception as e:
        logger.error(f"Image edit proxy error: {e}")
        return Response(
            {'error': f'Editing failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
