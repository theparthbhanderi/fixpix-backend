from rest_framework import viewsets, status, generics
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.core.files.base import ContentFile
import time
import os
import base64
from .models import ImageProject, ContactMessage
from .services.link_service import extract_article_content
from .services.search_service import multi_source_search
from .services.trends_service import get_live_trends
from .services import factcheck_service
from .services.cloudflare_image import generate_image
from .serializers import ImageProjectSerializer, RegisterSerializer, UserSerializer, MyTokenObtainPairSerializer, ContactMessageSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from subscriptions.utils import check_and_log_usage
from rest_framework.views import APIView
from .google_auth import verify_google_oauth2_token, get_or_create_user_from_google, get_tokens_for_user

class GoogleLoginView(APIView):
    permission_classes = (AllowAny,)
    
    def post(self, request):
        credential = request.data.get('credential')
        if not credential:
            return Response({'error': 'Missing Google credential'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            idinfo = verify_google_oauth2_token(credential)
            user = get_or_create_user_from_google(idinfo)
            tokens = get_tokens_for_user(user)
            
            return Response({
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                },
                'tokens': tokens
            })
        except ValueError as e:
            print(f"Google Login ValueError: {str(e)}")
            return Response({'error': f"Token Verification Failed: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'error': f"Internal Server Error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

class RegisterView(generics.CreateAPIView):
    queryset = UserSerializer.Meta.model.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = RegisterSerializer


class ProfileView(generics.RetrieveUpdateAPIView):
    """User profile endpoint — GET to retrieve, PUT/PATCH to update."""
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    def get(self, request, *args, **kwargs):
        user = request.user
        images_count = ImageProject.objects.filter(user=user).count()
        avatar_url = None
        if hasattr(user, 'profile') and user.profile.avatar:
            avatar_url = request.build_absolute_uri(user.profile.avatar.url)

        # ─── SaaS Subscription Sync ─────────────────────────────────
        from subscriptions.utils import get_user_plan, get_usage_stats
        plan_name = get_user_plan(user)
        usage_stats = get_usage_stats(user)

        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'date_joined': user.date_joined.isoformat(),
            'first_name': user.first_name,
            'last_name': user.last_name,
            'images_count': images_count,
            'avatar_url': avatar_url,
            # SaaS Extensions: Proxy metadata for AuthContext SSOT
            'plan': plan_name,
            'usage': usage_stats,
            'user_metadata': {
                'fixpix_plan': plan_name,
                'fixpix_plan_status': 'active'
            }
        })

    def put(self, request, *args, **kwargs):
        user = request.user
        data = request.data

        # Handle password change
        if 'new_password' in data:
            old_password = data.get('old_password', '')
            if not user.check_password(old_password):
                return Response({'error': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
            new_password = data['new_password']
            if len(new_password) < 6:
                return Response({'error': 'Password must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)
            user.set_password(new_password)
            user.save()
            return Response({'message': 'Password changed successfully.'})

        if 'username' in data:
            new_username = data['username'].strip()
            if new_username and new_username != user.username:
                from django.contrib.auth.models import User as UserModel
                if UserModel.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                    return Response({'error': 'Username already taken.'}, status=status.HTTP_400_BAD_REQUEST)
                user.username = new_username

        if 'email' in data:
            user.email = data['email'].strip()

        if 'first_name' in data:
            user.first_name = data['first_name'].strip()

        if 'last_name' in data:
            user.last_name = data['last_name'].strip()

        user.save()

        # Handle Avatar
        if 'avatar' in request.FILES:
            if not hasattr(user, 'profile'):
                from .models import UserProfile
                UserProfile.objects.create(user=user)
            user.profile.avatar = request.FILES['avatar']
            user.profile.save()

        # Return updated tokens so frontend stays in sync
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)
        # Add custom claims to match MyTokenObtainPairSerializer
        refresh['username'] = user.username
        refresh['email'] = user.email
        refresh['is_staff'] = user.is_staff
        refresh['is_superuser'] = user.is_superuser

        images_count = ImageProject.objects.filter(user=user).count()
        avatar_url = None
        if hasattr(user, 'profile') and user.profile.avatar:
            avatar_url = request.build_absolute_uri(user.profile.avatar.url)

        return Response({
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'date_joined': user.date_joined.isoformat(),
                'images_count': images_count,
                'avatar_url': avatar_url,
            },
            'tokens': {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }
        })

    patch = put  # Support PATCH as well

    def delete(self, request, *args, **kwargs):
        """Delete user account and all associated data."""
        user = request.user
        # Delete all user's images
        ImageProject.objects.filter(user=user).delete()
        # Delete the user
        user.delete()
        return Response({'message': 'Account deleted successfully.'}, status=status.HTTP_200_OK)

class ImageViewSet(viewsets.ModelViewSet):
    serializer_class = ImageProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ImageProject.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def detect_faces(self, request):
        """
        Detect faces in the provided image and return their coordinates.
        Expects a 'file' or 'image' in multipart/form-data.
        """
        image_file = request.FILES.get('file') or request.FILES.get('image')
        if not image_file:
            return Response({'error': 'No image file provided'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            from .ai_engine import AIEngine
            result = AIEngine.detect_faces(image_file)
            
            return Response({
                'status': 'success',
                'faces': result['faces'],
                'imageSize': result['imageSize']
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def restore_face(self, request):
        """
        Synchronous face restoration for the live editor tool.
        Expects a 'file' in multipart/form-data.
        Returns: { 'restored_image': 'base64_data' }
        """
        image_file = request.FILES.get('file')
        if not image_file:
            return Response({'error': 'No image file provided'}, status=status.HTTP_400_BAD_REQUEST)

        # Check Usage Limit
        usage = check_and_log_usage(request.user, "photo_restoration")
        if not usage["allowed"]:
            return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)
            
        fidelity = float(request.data.get('fidelity', 0.5))
        preserve_skin_tone = request.data.get('preserve_skin_tone', 'false').lower() == 'true'
        
        try:
            from .ai_engine import AIEngine
            from subscriptions.utils import get_user_plan
            plan_name = get_user_plan(request.user)
            
            restored_img = AIEngine.restore_faces(image_file, return_path=False, fidelity=fidelity, preserve_skin_tone=preserve_skin_tone, plan_name=plan_name)
            
            # Encode to jpeg base64
            import cv2
            import base64
            # If watermark was applied, it's already in restored_img
            # But wait, restore_faces doesn't call _save_result when return_path=False
            _, buffer = cv2.imencode('.jpg', restored_img)
            base64_img = base64.b64encode(buffer).decode('utf-8')
            
            return Response({
                'status': 'success',
                'restored_image': base64_img
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def inpaint(self, request):
        """
        Synchronous object removal (inpainting).
        Expects 'image' and 'mask' in multipart/form-data.
        Returns: { 'inpainted_image': 'base64_data' }
        """
        image_file = request.FILES.get('image')
        mask_file = request.FILES.get('mask')
        prompt = request.data.get('prompt', 'remove the masked object completely and seamlessly fill the background with high quality, matching textures')

        if not image_file or not mask_file:
            return Response({'error': 'Image and Mask are required'}, status=status.HTTP_400_BAD_REQUEST)

        # Check Usage Limit
        usage = check_and_log_usage(request.user, "background_remove")
        if not usage["allowed"]:
            return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)

        try:
            from .ai_engine import AIEngine
            import cv2
            import base64
            import numpy as np

            # Read bytes
            image_bytes = image_file.read()
            mask_bytes = mask_file.read()

            # Process via AIEngine (which calls Stability AI)
            # We want the result as numpy or bytes
            # For efficiency in this sync endpoint, let's call stability directly or via AIEngine with return_path=False
            
            # Prepare for AIEngine
            nparr_img = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr_img, cv2.IMREAD_COLOR)

            nparr_mask = np.frombuffer(mask_bytes, np.uint8)
            mask = cv2.imdecode(nparr_mask, cv2.IMREAD_UNCHANGED)

            result_img = AIEngine.inpaint_object(img, mask, return_path=False, prompt=prompt)

            # Encode result
            success, buffer = cv2.imencode('.jpg', result_img)
            if not success:
                 return Response({'error': 'Failed to encode inpaint result'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            base64_res = base64.b64encode(buffer).decode('utf-8')

            return Response({
                'status': 'success',
                'inpainted_image': base64_res
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

        # Check Usage Limit based on active feature
        feature_key = "photo_restoration" # Default
        if settings_data.get('colorize'):
            feature_key = "colorization"
        elif settings_data.get('upscaleX'):
            feature_key = "upscaling"
        elif request.data.get('mask') or settings_data.get('removeObject'):
            feature_key = "background_remove"
        
        usage = check_and_log_usage(request.user, feature_key)
        if not usage["allowed"]:
            return Response(usage, status=status.HTTP_429_TOO_MANY_REQUESTS)

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

    @action(detail=True, methods=['get'], permission_classes=[IsAuthenticated])
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
            # Enforce ownership check (Security Audit Fix)
            project = ImageProject.objects.get(pk=pk)
            if project.user != request.user:
                return Response({'error': 'Unauthorized access to this asset'}, status=status.HTTP_403_FORBIDDEN)
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
                # Apply Watermark for Free Tier if not already present
                # (Or always apply if free, to be safe for legacy images)
                from subscriptions.utils import get_user_plan
                plan_name = get_user_plan(project.user)
                if plan_name == 'free':
                    import numpy as np
                    import cv2
                    from .utils.watermark import apply_watermark
                    
                    # Convert PIL to CV2
                    img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                    img_wm = apply_watermark(img_cv)
                    # Convert back to PIL
                    img = Image.fromarray(cv2.cvtColor(img_wm, cv2.COLOR_BGR2RGB))

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


@csrf_exempt
def text_to_image(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            prompt = data.get("prompt")

            if not prompt:
                return JsonResponse({"error": "Prompt required"}, status=400)

            image = generate_image(prompt)

            if not image:
                return JsonResponse({"error": "Generation failed"}, status=500)

            return HttpResponse(image, content_type="image/jpeg")
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
    return JsonResponse({"error": "Method not allowed"}, status=405)


# --- Supabase Migration ViewSets ---

from .models import EditHistory, ChatHistory, WorkflowHistory
from .serializers import EditHistorySerializer, ChatHistorySerializer, WorkflowHistorySerializer

class EditHistoryViewSet(viewsets.ModelViewSet):
    serializer_class = EditHistorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return EditHistory.objects.filter(user=self.request.user).order_by('-created_at')
        
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class ChatHistoryViewSet(viewsets.ModelViewSet):
    serializer_class = ChatHistorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return ChatHistory.objects.filter(user=self.request.user).order_by('timestamp')
        
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class WorkflowHistoryViewSet(viewsets.ModelViewSet):
    serializer_class = WorkflowHistorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return WorkflowHistory.objects.filter(user=self.request.user).order_by('-created_at')
        
    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ContactSubmissionView(generics.CreateAPIView):
    queryset = ContactMessage.objects.all()
    serializer_class = ContactMessageSerializer
    permission_classes = [AllowAny]


