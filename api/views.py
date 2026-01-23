from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.files.base import ContentFile
import time
import os
import base64
from .models import ImageProject
from .serializers import ImageProjectSerializer, RegisterSerializer, UserSerializer, MyTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

class RegisterView(generics.CreateAPIView):
    queryset = UserSerializer.Meta.model.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = RegisterSerializer

class ImageViewSet(viewsets.ModelViewSet):
    serializer_class = ImageProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ImageProject.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'])
    def process_image(self, request, pk=None):
        project = self.get_object()
        
        # Determine settings from request body
        settings_data = request.data.get('settings', {})
        print(f"DEBUG: Process Image Async Request. Settings: {settings_data}")
        
        # Fallback to legacy processing_type if settings empty
        if not settings_data and project.processing_type:
             algo_type = project.processing_type
             if algo_type == 'restore': settings_data['removeScratches'] = True
             if algo_type == 'colorize': settings_data['colorize'] = True
             if algo_type == 'upscale': settings_data['upscaleX'] = 2

        # Basic Validation (Security)
        # Cap upscale to 4x to prevent DOS
        if 'upscaleX' in settings_data:
             try:
                 val = int(settings_data['upscaleX'])
                 if val > 4: settings_data['upscaleX'] = 4
             except:
                 settings_data['upscaleX'] = 1

        # Handle Mask for Inpainting
        mask_temp_path = None
        mask_data = request.data.get('mask')
        if mask_data:
             try:
                # Store mask in temp storage (needs to be accessible by worker)
                from django.core.files.storage import default_storage
                from django.core.files.base import ContentFile
                import time
                
                if 'base64,' in mask_data:
                    mask_data = mask_data.split('base64,')[1]
                
                mask_content = base64.b64decode(mask_data)
                mask_filename = f"temp/mask_{pk}_{int(time.time())}.png"
                
                # Save to storage
                mask_temp_path = default_storage.save(mask_filename, ContentFile(mask_content))
             except Exception as e:
                 print(f"Mask upload failed: {e}")

        # Update status to pending
        project.status = 'pending'
        project.save()

        # Dispatch Async Task
        try:
            from .tasks import process_image_async
            task = process_image_async.delay(project.id, settings_data, mask_temp_path)
        except Exception as e:
            # Fallback if Broker is down
            print(f"Celery Error: {e}")
            project.status = 'failed'
            project.save()
            return Response({
                'error': 'Processing service is currently unavailable. Please try again later.',
                'detail': str(e)
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        return Response({
            'status': 'accepted', 
            'task_id': task.id,
            'message': 'Image processing started in background.'
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'], permission_classes=[AllowAny])
    def download(self, request, pk=None):
        """
        Serve the processed image as a downloadable attachment.
        Supports format conversion and quality control.
        Query Params:
        - format: 'png', 'jpg', 'jpeg', 'webp' (default: original ext or png)
        - quality: 1-100 (default: 90)
        """
        from django.http import FileResponse, HttpResponse
        import mimetypes
        from PIL import Image
        import io
        
        try:
            # Bypass get_object() which filters by user (since we are AllowAny now)
            project = ImageProject.objects.get(pk=pk)
        except ImageProject.DoesNotExist:
            return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if not project.processed_image:
             return Response({'error': 'No processed image available'}, status=status.HTTP_404_NOT_FOUND)
             
        file_path = project.processed_image.path
        if not os.path.exists(file_path):
            return Response({'error': 'File not found on server'}, status=status.HTTP_404_NOT_FOUND)

        # Parse Query Params
        target_format = request.query_params.get('format', '').lower()
        quality_param = request.query_params.get('quality', '90')
        
        try:
            quality = int(quality_param)
            quality = max(1, min(100, quality))
        except ValueError:
            quality = 90

        # If no specific format requested, serve raw file (fastest) unless resizing/re-encoding needed?
        # Actually, let's always use PIL if format/quality is specified to ensure it applies.
        # But if format is empty and quality is default, serve raw.
        original_ext = os.path.splitext(file_path)[1].lower().replace('.', '')
        if original_ext == 'jpeg': original_ext = 'jpg'
        
        if not target_format:
            target_format = original_ext
        if target_format == 'jpeg': target_format = 'jpg'

        # Optimize: If requested format matches original AND quality is high/default, serve distinct file
        # But user might want to Compress (quality 50) same format.
        # Simple Logic: Open, Convert, Save to Buffer.
        
        try:
            with Image.open(file_path) as img:
                # Convert RGBA to RGB if saving as JPEG
                if target_format == 'jpg' and img.mode == 'RGBA':
                    img = img.convert('RGB')
                
                # Buffer
                buffer = io.BytesIO()
                
                # Save mapping
                pil_format = target_format.upper()
                if pil_format == 'JPG': pil_format = 'JPEG'
                
                # Save parameters
                save_params = {'format': pil_format}
                if target_format in ['jpg', 'jpeg', 'webp']:
                    save_params['quality'] = quality
                    # Optimize for webp
                    if target_format == 'webp':
                        save_params['method'] = 6
                
                img.save(buffer, **save_params)
                buffer.seek(0)
                
                # Generate filename
                timestamp = int(time.time())
                filename = f"fixpix_export_{timestamp}.{target_format}"
                content_type = mimetypes.guess_type(filename)[0] or f'image/{target_format}'
                
                return FileResponse(buffer, as_attachment=True, filename=filename, content_type=content_type)
        except Exception as e:
            print(f"Export Error: {e}")
            return Response({'error': 'Error generating export'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def generate(self, request):
        """
        Generate an image from a text prompt using DeepFloyd IF.
        
        Request Body:
        - prompt: str (required) - Text description of image to generate
        - style: str (optional) - 'photorealistic', 'artistic', or 'anime' (default: photorealistic)
        - seed: int (optional) - Random seed for reproducibility
        
        Returns 202 Accepted with project_id for polling.
        """
        from .generation_limits import GenerationLimits
        
        # Extract parameters
        prompt = request.data.get('prompt', '').strip()
        style = request.data.get('style', 'photorealistic')
        seed = request.data.get('seed')
        
        # Validate seed if provided
        if seed is not None:
            try:
                seed = int(seed)
            except (ValueError, TypeError):
                seed = None
        
        # Check rate limits
        limits = GenerationLimits(request.user)
        limit_check = limits.can_generate()
        
        if not limit_check['allowed']:
            return Response({
                'error': limit_check['reason'],
                'remaining': limit_check['remaining'],
                'daily_limit': limit_check['daily_limit'],
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # Validate prompt
        prompt_check = limits.validate_prompt(prompt)
        if not prompt_check['valid']:
            return Response({
                'error': prompt_check['error'],
            }, status=status.HTTP_400_BAD_REQUEST)
        
        sanitized_prompt = prompt_check['sanitized']
        
        # Validate style
        valid_styles = ['photorealistic', 'artistic', 'anime']
        if style not in valid_styles:
            style = 'photorealistic'
        
        # Create project with source='generated'
        project = ImageProject.objects.create(
            user=request.user,
            source='generated',
            prompt=sanitized_prompt,
            gen_style=style,
            gen_seed=seed,
            status='pending',
            processing_type='restore',  # Required field, but not used for generation
        )
        
        # Increment concurrent count
        limits.increment_concurrent()
        
        # Dispatch async generation task
        try:
            from .tasks import generate_image_async
            task = generate_image_async.delay(
                str(project.id),
                sanitized_prompt,
                style,
                seed
            )
        except Exception as e:
            print(f"Celery Error (generation): {e}")
            limits.decrement_concurrent()
            project.status = 'failed'
            project.save()
            return Response({
                'error': 'Generation service is currently unavailable. Please try again later.',
                'detail': str(e)
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
        return Response({
            'status': 'accepted',
            'project_id': str(project.id),
            'task_id': task.id,
            'message': 'Image generation started. This may take 2-3 minutes.',
            'remaining': limit_check['remaining'] - 1,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['get'])
    def generation_status(self, request):
        """
        Get user's generation limit status.
        
        Returns current usage, limits, and tier info.
        """
        from .generation_limits import GenerationLimits
        
        limits = GenerationLimits(request.user)
        return Response(limits.get_status())

