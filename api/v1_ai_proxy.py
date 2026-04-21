import base64
import cv2
import numpy as np
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .ai_engine import AIEngine
from subscriptions.plan_enforcement import check_user_plan_for_request, record_successful_usage

logger = logging.getLogger(__name__)

def _file_to_np(file):
    """Helper to convert uploaded file to numpy array."""
    try:
        file.seek(0)
        file_bytes = np.frombuffer(file.read(), np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if img is None:
            logger.error("Failed to decode image in _file_to_np")
        return img
    except Exception as e:
        logger.error(f"Error in _file_to_np: {e}")
        return None

def _np_to_base64(img, ext='.jpg'):
    """Helper to convert numpy array to base64 string."""
    try:
        _, buffer = cv2.imencode(ext, img)
        return base64.b64encode(buffer).decode('utf-8')
    except Exception as e:
        logger.error(f"Error in _np_to_base64: {e}")
        return ""

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def super_resolution_v1(request):
    """
    Drop-in replacement for FastAPI /api/v1/image/super-resolution.
    Expects: multipart 'file' and 'scale'.
    Returns: { "upscaled_image": base64 }
    """
    file = request.FILES.get('file') or request.FILES.get('image')
    scale = request.data.get('scale', 2)
    
    if not file:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    plan_check = check_user_plan_for_request(request, feature_key="upscaling", consume=False)
    if not plan_check.get("allowed"):
        return Response(plan_check, status=plan_check.get("status_code", status.HTTP_403_FORBIDDEN))
    
    try:
        scale = int(scale)
        plan_name = str(plan_check.get("plan", "FREE")).lower()
        # Support 2x, 4x, and 8x
        valid_scales = [2, 4, 8]
        scale_warning = None
        if scale not in valid_scales:
            # If not standard, snap to nearest valid or 4x
            if scale > 8: scale = 8
            elif scale < 2: scale = 2
            else: scale = 4
            scale_warning = f"Scale adjusted to {scale}x (unsupported value provided)"
            logger.info(f"SR V1: {scale_warning}")

        img = _file_to_np(file)
        if img is None:
            return Response({'error': 'Could not decode image data'}, status=status.HTTP_400_BAD_REQUEST)
            
        # upscale_image now uses Stability AI if available
        result = AIEngine.upscale_image(img, scale=scale, return_path=False, plan_name=plan_name)
        
        if result is None:
            logger.error("AIEngine.upscale_image returned None")
            return Response({'error': 'Super Resolution processing failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        response_data = {
            'upscaled_image': _np_to_base64(result),
            'status': 'success',
            'plan': plan_check.get("plan"),
            'priority': plan_check.get("priority", "low")
        }
        if scale_warning:
            response_data['message'] = scale_warning
        record_successful_usage(request, feature_key="upscaling")
        return Response(response_data)
    except Exception as e:
        logger.error(f"SR V1 Proxy Error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def inpaint_v1(request):
    """
    Drop-in replacement for FastAPI /api/v1/image/inpaint.
    Expects: multipart 'image' and 'mask'.
    Returns: { "inpainted_image": base64 }
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    mask_file = request.FILES.get('mask') or request.FILES.get('mask_file')
    
    if not image_file or not mask_file:
        return Response({'error': 'Image or mask missing'}, status=status.HTTP_400_BAD_REQUEST)

    plan_check = check_user_plan_for_request(request, feature_key="background_remove", consume=False)
    if not plan_check.get("allowed"):
        return Response(plan_check, status=plan_check.get("status_code", status.HTTP_403_FORBIDDEN))
    
    try:
        plan_name = str(plan_check.get("plan", "FREE")).lower()
        img = _file_to_np(image_file)
        mask = _file_to_np(mask_file)
        
        if img is None or mask is None:
            return Response({'error': 'Could not decode image or mask data'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Convert mask to grayscale if needed
        if len(mask.shape) == 3:
            mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
            
        # inpaint_object now uses Stability AI if available
        result = AIEngine.inpaint_object(img, mask, return_path=False, plan_name=plan_name)
        
        if result is None:
            logger.error("AIEngine.inpaint_object returned None")
            return Response({'error': 'Inpainting processing failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        record_successful_usage(request, feature_key="background_remove")
        return Response({
            'inpainted_image': _np_to_base64(result),
            'status': 'success',
            'plan': plan_check.get("plan"),
            'priority': plan_check.get("priority", "low")
        })
    except Exception as e:
        logger.error(f"Inpaint V1 Proxy Error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def colorize_v1(request):
    """
    Drop-in replacement for FastAPI /api/v1/image/colorize.
    """
    image_file = request.FILES.get('image') or request.FILES.get('file')
    if not image_file:
        return Response({'error': 'No image uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    plan_check = check_user_plan_for_request(request, feature_key="edit", consume=False)
    if not plan_check.get("allowed"):
        return Response(plan_check, status=plan_check.get("status_code", status.HTTP_403_FORBIDDEN))
        
    try:
        plan_name = str(plan_check.get("plan", "FREE")).lower()
        img = _file_to_np(image_file)
        # We don't have a Stability colorizer yet, so we use OpenCV/Engine default
        # But we could easily add one to AIEngine later.
        result = AIEngine.colorize_image(img, return_path=False, plan_name=plan_name)
        
        if result is None:
            return Response({'error': 'Colorization failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        record_successful_usage(request, feature_key="edit")
        return Response({
            'colorized_image': _np_to_base64(result),
            'status': 'success',
            'plan': plan_check.get("plan"),
            'priority': plan_check.get("priority", "low")
        })
    except Exception as e:
        logger.error(f"Colorize V1 Proxy Error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
