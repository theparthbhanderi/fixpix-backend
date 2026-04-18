"""
Stability AI Proxy Views

Direct proxy endpoints for Stability AI features:
- Remove Background
- Colorize Image (via Structure Control)
- Conservative Upscale
"""

import base64
import logging
from subscriptions.utils import check_and_log_usage
from io import BytesIO
from PIL import Image

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)

# Stability AI max limit: 9,437,184 pixels (approx 4096x2304)
MAX_PIXELS = 9_400_000

def resize_image_if_needed(image_bytes):
    """
    Resizes the image if it exceeds Stability AI's 9.4 Megapixel limit.
    Maintains aspect ratio.
    """
    try:
        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        total_pixels = width * height
        
        if total_pixels <= MAX_PIXELS:
            return image_bytes
            
        # Calculate scaling factor
        scale_factor = (MAX_PIXELS / total_pixels) ** 0.5
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        
        logger.info(f"Resizing image from {width}x{height} ({total_pixels}px) to {new_width}x{new_height} to meet Stability limits.")
        
        # Resize using high quality Lanczos filter
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Save to memory bytes
        output = BytesIO()
        img_format = img.format or 'PNG'
        img.save(output, format=img_format)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Error resizing image: {e}")
        return image_bytes  # fallback to original if resize fails


class StabilityRateThrottle(UserRateThrottle):
    rate = '300/minute'


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([StabilityRateThrottle])
def remove_background_view(request):
    """
    Remove background from an uploaded image.

    POST /api/v1/image/remove-bg/
    Body (Multipart): image file
    Returns: { "image": "data:image/png;base64,..." }
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    if not image_file:
        return Response(
            {'error': 'Please upload an image'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check Usage Limit
    usage = check_and_log_usage(request.user, "background_remove")
    if not usage["allowed"]:
        return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)

    try:
        from .ai_engine import AIEngine

        image_bytes = image_file.read()
        # Ensure we don't exceed API limits
        image_bytes = resize_image_if_needed(image_bytes)
        
        # Use AIEngine for Smart Multi-Layer Processing (Photoroom -> Stability -> Local)
        # We pass return_path=False to get the processed image bytes/numpy
        result_img = AIEngine.remove_background(image_bytes, return_path=False)

        if result_img is None:
            return Response(
                {'error': 'Background removal failed. All AI services are currently unavailable.'},
                status=status.HTTP_502_BAD_GATEWAY
            )
        
        # Encode result (which is a numpy array from cv2) to PNG bytes
        import cv2
        success, buffer = cv2.imencode('.png', result_img)
        if not success:
            return Response({'error': 'Failed to encode result image'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        result = buffer.tobytes()

        image_b64 = base64.b64encode(result).decode('utf-8')
        data_url = f"data:image/png;base64,{image_b64}"

        return Response({
            'image': data_url,
            'status': 'success',
            'engine': 'smart-ai-nobg'
        })

    except Exception as e:
        logger.error(f"Remove Background error: {e}")
        return Response(
            {'error': f'Background removal failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([StabilityRateThrottle])
def change_background_view(request):
    """
    Change background of an uploaded image to a new generated scene.

    POST /api/v1/image/change-bg/
    Body (Multipart): image file, prompt
    Returns: { "image": "data:image/webp;base64,..." }
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    prompt = request.data.get('prompt', 'a natural setting')
    
    if not image_file:
        return Response(
            {'error': 'Please upload an image'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check Usage Limit
    usage = check_and_log_usage(request.user, "background_remove")
    if not usage["allowed"]:
        return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)

    try:
        from .services.stability_service import StabilityService

        image_bytes = image_file.read()
        image_bytes = resize_image_if_needed(image_bytes)
        
        stability = StabilityService()
        result = stability.change_background(image_bytes, prompt)

        if not result:
            return Response(
                {'error': 'Background change failed. All API keys may be exhausted.'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        image_b64 = base64.b64encode(result).decode('utf-8')
        data_url = f"data:image/webp;base64,{image_b64}"

        return Response({
            'image': data_url,
            'status': 'success',
            'engine': 'stability-change-bg'
        })

    except Exception as e:
        logger.error(f"Change Background error: {e}")
        return Response(
            {'error': f'Background change failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([StabilityRateThrottle])
def style_transfer_view(request):
    """
    Apply style from a reference image or prompt to a base image.

    POST /api/v1/image/style-transfer/
    Body (Multipart): 
    - image: base image file
    - style_image: optional reference style image file
    - prompt: text prompt
    - negative_prompt: text negative prompt
    - style_strength: 0.0 - 1.0
    - composition_fidelity: 0.0 - 1.0
    - change_strength: 0.0 - 1.0
    - seed: int
    - output_format: jpeg/png/webp
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    style_image_file = request.FILES.get('style_image')
    
    prompt = request.data.get('prompt', '')
    negative_prompt = request.data.get('negative_prompt', '')
    
    try:
        style_strength = float(request.data.get('style_strength', 0.5))
        composition_fidelity = float(request.data.get('composition_fidelity', 0.5))
        change_strength = float(request.data.get('change_strength', 0.5))
        seed = int(request.data.get('seed', 0))
    except (ValueError, TypeError):
        style_strength = 0.5
        composition_fidelity = 0.5
        change_strength = 0.5
        seed = 0
        
    output_format = request.data.get('output_format', 'jpeg')
    
    # M5 FIX: Handle preserve_face toggle from frontend
    preserve_face = request.data.get('preserve_face', False)
    if isinstance(preserve_face, str):
        preserve_face = preserve_face.lower() in ('true', '1', 'yes')

    if not image_file:
        return Response(
            {'error': 'Please upload a base image'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        from .services.stability_service import StabilityService

        image_bytes = image_file.read()
        image_bytes = resize_image_if_needed(image_bytes)
        
        style_image_bytes = None
        if style_image_file:
            style_image_bytes = style_image_file.read()
            style_image_bytes = resize_image_if_needed(style_image_bytes)

        stability = StabilityService()
        
        # M5 FIX: Augment prompt when preserve_face is enabled
        effective_prompt = prompt
        if preserve_face and prompt:
            effective_prompt = f"{prompt}, preserve original face features and facial details"

        result = stability.style_transfer(
            image_bytes=image_bytes,
            style_image_bytes=style_image_bytes,
            prompt=effective_prompt,
            negative_prompt=negative_prompt,
            style_strength=style_strength,
            composition_fidelity=composition_fidelity,
            change_strength=change_strength,
            seed=seed,
            output_format=output_format
        )

        if not result:
            return Response(
                {'error': 'Style Transfer failed. All API keys may be exhausted.'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        image_b64 = base64.b64encode(result).decode('utf-8')
        mime_type = "image/jpeg" if output_format == "jpeg" else f"image/{output_format}"
        data_url = f"data:{mime_type};base64,{image_b64}"

        return Response({
            'image': data_url,
            'status': 'success',
            'engine': 'stability-style-transfer'
        })

    except Exception as e:
        logger.error(f"Style Transfer error: {e}")
        return Response(
            {'error': f'Style Transfer failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([StabilityRateThrottle])
def conservative_upscale_view(request):
    """
    Upscale image to 4K using Stability Conservative Upscaler.

    POST /api/v1/image/upscale-conservative/
    Body (Multipart): image file, optional prompt
    Returns: { "image": "data:image/webp;base64,...", "upscaled_image": base64 }
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    prompt = (request.data.get('prompt') or 'High quality, detailed, 4K resolution').strip()

    if not image_file:
        return Response(
            {'error': 'Please upload an image'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Check Usage Limit
    usage = check_and_log_usage(request.user, "upscaling")
    if not usage["allowed"]:
        return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)

    try:
        from .services.stability_service import StabilityService

        image_bytes = image_file.read()
        image_bytes = resize_image_if_needed(image_bytes)
        
        stability = StabilityService()

        # Try Conservative Upscaler first (sync, fast)
        result = stability.conservative_upscale(image_bytes, prompt)

        # Fallback to Creative Upscaler if Conservative fails
        if not result:
            logger.info("Conservative Upscaler failed, falling back to Creative Upscaler...")
            result = stability.upscale_image(image_bytes, prompt)

        if not result:
            return Response(
                {'error': 'Upscaling failed. All API keys may be exhausted.'},
                status=status.HTTP_502_BAD_GATEWAY
            )

        image_b64 = base64.b64encode(result).decode('utf-8')
        data_url = f"data:image/webp;base64,{image_b64}"

        return Response({
            'image': data_url,
            'upscaled_image': image_b64,
            'status': 'success',
            'engine': 'stability-conservative-upscale'
        })

    except Exception as e:
        logger.error(f"Upscale error: {e}")
        return Response(
            {'error': f'Upscaling failed: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_sticker_view(request):
    """
    Magic Sticker Generator with Seamless Alternative Fallback.
    """
    prompt = request.data.get('prompt', '')
    style = request.data.get('style', 'cartoon')
    outline_width = int(request.data.get('outlineWidth', 10))
    outline_color_name = request.data.get('outlineColor', 'white')

    # Color Mapping
    COLOR_MAP = {
        'white': (255, 255, 255),
        'black': (0, 0, 0),
        'neon_green': (57, 255, 20),
        'neon_pink': (255, 16, 240),
        'neon_blue': (0, 191, 255),
    }
    outline_color = COLOR_MAP.get(outline_color_name, (255, 255, 255))
    
    if not prompt:
        return Response({'error': 'Prompt is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Check Usage Limit
    usage = check_and_log_usage(request.user, "image_generation")
    use_alternative = not usage["allowed"]

    # 1. GENERATE STICKER VIA MULTI-PROVIDER SERVICE
    try:
        from .services.multi_provider_service import MultiProviderService
        from .ai_engine import AIEngine
        
        logger.info(f"StabilityViews: Generating resilient sticker for: {prompt} (Style: {style})")
        
        result = MultiProviderService.generate_sticker(
            prompt=prompt,
            style=style,
            outline_width=outline_width,
            outline_color=outline_color
        )
        
        data_url = result["data_url"]
        used_engine = result["engine"]
        
        # 2. SAVE & LOG HISTORY
        # We save the processed image to disk
        output_path = AIEngine._save_result(result["raw_image"], f"sticker_{used_engine}_{prompt[:10]}.png", "sticker", return_path=True)
        
        AIEngine.log_edit_history(
            user=request.user,
            tool="sticker_magic",
            parameters={"prompt": prompt, "style": style, "engine": used_engine, "fallback": used_engine != "stability"},
            output_url=output_path
        )

        return Response({
            'sticker': data_url,
            'image': data_url,
            'status': 'success',
            'engine': used_engine,
            'saved_path': output_path,
            'is_alternative': used_engine != "stability"
        })

    except Exception as e:
        logger.error(f"Generate Sticker error: {e}")
        return Response({
            'error': str(e),
            'message': f'Sticker generation failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
