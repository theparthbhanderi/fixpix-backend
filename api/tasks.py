"""
Celery Tasks for FixPix

Background tasks for heavy AI image processing operations.
These run in Celery workers, not the main web server.
"""

try:
    from celery import shared_task
except ImportError:
    # Fallback for Vercel/No-Celery environments
    # Creates a dummy decorator that runs tasks synchronously
    import uuid
    
    class MockAsyncResult:
        """Mock Celery AsyncResult for synchronous execution."""
        def __init__(self, result):
            self.id = str(uuid.uuid4())  # Generate a fake task ID
            self.result = result
            self.status = 'SUCCESS'
    
    class MockTask:
        """Mock Celery Task object for bind=True tasks."""
        def __init__(self):
            self.request = type('obj', (object,), {'id': str(uuid.uuid4())})()
        
        def retry(self, *args, **kwargs):
            """Mock retry - just raise the exception since we can't actually retry."""
            exc = kwargs.get('exc')
            if exc:
                raise exc
    
    def shared_task(*args, **kwargs):
        bind = kwargs.get('bind', False)
        
        def decorator(func):
            def wrapper(*f_args, **f_kwargs):
                if bind:
                    # Pass mock task as first argument when bind=True
                    return func(MockTask(), *f_args, **f_kwargs)
                return func(*f_args, **f_kwargs)
            
            # Mock .delay() method to run synchronously and return mock result
            def mock_delay(*d_args, **d_kwargs):
                if bind:
                    # Pass mock task as first argument when bind=True
                    result = func(MockTask(), *d_args, **d_kwargs)
                else:
                    result = func(*d_args, **d_kwargs)
                return MockAsyncResult(result)
            
            wrapper.delay = mock_delay
            return wrapper
        return decorator

from django.conf import settings
import os
import time

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_image_async(self, image_id, settings_data, mask_path_temp=None):
    """
    Async task to process an image with AI engine.
    Optimized for Parallel Execution and High-Fidelity Results.
    """
    from api.models import ImageProject
    from api.ai_engine import AIEngine
    from django.core.files.storage import default_storage
    from concurrent.futures import ThreadPoolExecutor
    import time
    
    try:
        project = ImageProject.objects.get(id=image_id)
        project.status = 'processing'
        project.save()
        
        if not project.original_image:
             raise ValueError("No original image found")

        # Start timer for internal monitoring
        start_time = time.time()
        
        # Read original image
        ref_path = project.original_image.name
        current_img = AIEngine._read_image(ref_path)
        
        # --- PHASE 1: PARALLEL API OPERATIONS (Saves 10-15s per run) ---
        # We run Background Removal and Scratch Removal (Stability) in parallel
        # because they are independent API calls on the original bytes.
        
        def run_bg_removal(img):
            if settings_data.get('removeBackground', False):
                try:
                    return AIEngine.remove_background(img, return_path=False)
                except Exception as e:
                    print(f"Parallel BG Removal Failed: {e}")
                    return None
            return None

        def run_scratch_removal(img):
            if settings_data.get('removeScratches', False):
                try:
                    return AIEngine.remove_scratches(img, return_path=False)
                except Exception as e:
                    print(f"Parallel Scratch Removal Failed: {e}")
                    return None
            return None

        # Execute API-heavy tasks in parallel threads (Boosted to 6 Workers)
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_bg = executor.submit(run_bg_removal, current_img)
            future_scratches = executor.submit(run_scratch_removal, current_img)
            
            # Wait for results
            bg_result = future_bg.result()
            scratch_result = future_scratches.result()

        # Merge Results (Order of Precedence: Scratch Fix > BG Remove)
        # If scratch_result exists, we use it as our new base
        if scratch_result is not None:
            current_img = scratch_result
        
        # If bg_result exists, we use its alpha channel/mask to modify current_img
        if bg_result is not None:
            # We want current_img (possibly restored) but with bg_result's transparency
            import cv2
            import numpy as np
            
            # Ensure both images are same size (they should be)
            if bg_result.shape[:2] == current_img.shape[:2]:
                if bg_result.shape[2] == 4: # PNG from Photoroom
                    # Extract alpha from bg_result
                    b, g, r, a = cv2.split(bg_result)
                    
                    # Ensure current_img is 4-channel
                    if current_img.shape[2] == 3:
                        cb, cg, cr = cv2.split(current_img)
                        current_img = cv2.merge([cb, cg, cr, a])
                    else:
                        cb, cg, cr, ca = cv2.split(current_img)
                        current_img = cv2.merge([cb, cg, cr, a])
                else:
                    # Fallback: Just take BG result as-is if no merging needed
                    current_img = bg_result

        # --- PHASE 2: SEQUENTIAL ENHANCEMENTS (Fast OpenCV / GFPGAN) ---
        
        # 1. Face Restoration (GFPGAN High-Fidelity)
        if settings_data.get('faceRestoration', False):
            fidelity = float(settings_data.get('fidelity', 0.8)) # Increased from 0.5
            current_img = AIEngine.restore_faces(current_img, return_path=False, fidelity=fidelity)
        
        # 2. Advanced Denoising (Clean up artifacts)
        denoise_strength = int(settings_data.get('denoiseStrength', 0))
        if denoise_strength > 0:
             current_img = AIEngine.denoise_advanced(current_img, strength=denoise_strength, return_path=False)

        # 3. Fast Local Adjustments (Color, White Balance, Contrast)
        # We batch these as they are near-instant OpenCV ops
        if settings_data.get('whiteBalance', False):
             current_img = AIEngine.correct_white_balance(current_img, return_path=False)

        if settings_data.get('autoEnhance', False):
             current_img = AIEngine.auto_enhance(current_img, return_path=False)

        b, c, s = map(lambda k: float(settings_data.get(k, 1.0)), ['brightness', 'contrast', 'saturation'])
        if any(v != 1.0 for v in [b, c, s]):
            current_img = AIEngine.adjust_image(current_img, brightness=b, contrast=c, saturation=s, return_path=False)

        # 4. Filter Preset
        filter_preset = settings_data.get('filterPreset', '')
        if filter_preset and filter_preset != 'none':
             current_img = AIEngine.apply_filter_preset(current_img, filter_preset, return_path=False)

        # 5. Object Removal (Inpainting - If mask exists)
        if mask_path_temp:
            try:
                if default_storage.exists(mask_path_temp):
                    with default_storage.open(mask_path_temp, 'rb') as f:
                        mask_bytes = f.read()
                    import numpy as np
                    import cv2
                    nparr = np.frombuffer(mask_bytes, np.uint8)
                    mask_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                    current_img = AIEngine.inpaint_object(current_img, mask_img, return_path=False)
                    default_storage.delete(mask_path_temp)
            except Exception as e:
                print(f"Parallel Inpainting failed: {e}")

        # 6. Final Upscaling (Last step to maximize detail)
        upscale_x = int(settings_data.get('upscaleX', 1))
        if upscale_x > 1:
             try:
                 current_img = AIEngine.upscale_image(current_img, scale=upscale_x, return_path=False)
             except Exception as e:
                 print(f"Final Upscale failed: {e}")

        # --- PIPELINE END ---
        
        # Log Speed Performance
        duration = time.time() - start_time
        print(f"🚀 Optimized Pipeline Complete in {duration:.2f}s (Project: {image_id})")

        # Final Save (to Storage)
        from subscriptions.plan_enforcement import get_or_create_user_plan
        plan_name = get_or_create_user_plan(project.user).plan.lower()
        final_rel_path = AIEngine._save_result(current_img, ref_path, 'edited', return_path=True, plan_name=plan_name)
        
        # Update project record
        project.processed_image.name = final_rel_path
        project.settings = settings_data
        project.status = 'completed'
        project.save()
        
        return {'status': 'success', 'image_id': image_id}
        
    except ImageProject.DoesNotExist:
        return {'status': 'error', 'message': 'Project not found'}
    except Exception as exc:
        # Only update project status if project was successfully fetched
        try:
            if 'project' in dir():
                project.status = 'failed'
                project.save()
        except:
            pass
        # Re-raise to propagate the error
        raise exc


@shared_task
def cleanup_old_processed_images(days=7):
    """
    Periodic task to clean up old processed images (files & records).
    Run via Celery Beat scheduler.
    """
    from api.models import ImageProject
    from django.utils import timezone
    from datetime import timedelta
    from django.core.files.storage import default_storage
    
    cutoff = timezone.now() - timedelta(days=days)
    old_projects = ImageProject.objects.filter(
        updated_at__lt=cutoff
    )
    
    deleted_count = 0
    for project in old_projects:
        # Delete files from storage
        if project.processed_image:
             default_storage.delete(project.processed_image.name)
        if project.original_image:
             # Be careful deleting originals if they are shared? 
             # Assuming projects own their original copy.
             default_storage.delete(project.original_image.name)
             
        project.delete()
        deleted_count += 1
                
    return f'Cleaned up {deleted_count} old projects'


@shared_task(bind=True, max_retries=2, time_limit=600, soft_time_limit=540)
def generate_image_async(self, project_id, prompt, style='photorealistic', seed=None):
    """
    Async task to generate an image using DeepFloyd IF.
    
    This task runs on the 'generation' queue which should be handled by
    GPU-equipped workers. In development/Vercel (no-celery), runs synchronously.
    
    Args:
        project_id: ID of the ImageProject
        prompt: Text prompt for generation
        style: One of 'photorealistic', 'artistic', 'anime'
        seed: Random seed for reproducibility (optional)
    """
    from api.models import ImageProject
    from api.deepfloyd_service import DeepFloydService
    from api.generation_limits import GenerationLimits
    from subscriptions.plan_enforcement import get_or_create_user_plan
    import time
    
    start_time = time.time()
    
    try:
        project = ImageProject.objects.get(id=project_id)
        project.status = 'processing'
        project.save()
        
        print(f"DeepFloyd Task: Starting generation for project {project_id}")
        print(f"DeepFloyd Task: Prompt: '{prompt[:50]}...', Style: {style}")

        user_plan = get_or_create_user_plan(project.user)
        plan_tier = user_plan.plan

        if plan_tier == 'ELITE':
            inference_steps = 60
            guidance_scale = 8.0
        elif plan_tier == 'PRO':
            inference_steps = 50
            guidance_scale = 7.5
        else:
            inference_steps = 35
            guidance_scale = 6.5
        
        # Initialize service and generate
        service = DeepFloydService()
        
        result = service.generate(
            prompt=prompt,
            style=style,
            seed=seed,
            num_inference_steps=inference_steps,
            guidance_scale=guidance_scale,
        )
        
        # Check for NSFW content
        if result.get('nsfw_detected', False):
            project.status = 'failed'
            project.save()
            # Don't count failed generations against limit
            limits = GenerationLimits(project.user)
            limits.record_generation(
                gpu_time_seconds=time.time() - start_time,
                success=False
            )
            return {
                'status': 'error',
                'message': 'Content policy violation detected. Please try a different prompt.',
                'project_id': project_id
            }
        
        # Update project with result
        project.processed_image.name = result['image_path']
        project.gen_seed = result['seed']
        project.gen_steps = result['steps']
        project.status = 'completed'
        project.save()
        
        # Record successful generation
        gpu_time = time.time() - start_time
        limits = GenerationLimits(project.user)
        limits.record_generation(gpu_time_seconds=gpu_time, success=True)
        
        print(f"DeepFloyd Task: Completed in {gpu_time:.1f}s")
        
        return {
            'status': 'success',
            'project_id': project_id,
            'image_path': result['image_path'],
            'seed': result['seed'],
        }
        
    except ImageProject.DoesNotExist:
        print(f"DeepFloyd Task: Project {project_id} not found")
        return {'status': 'error', 'message': 'Project not found'}
        
    except Exception as exc:
        print(f"DeepFloyd Task: Error - {exc}")
        
        # Update project status
        try:
            project = ImageProject.objects.get(id=project_id)
            project.status = 'failed'
            project.save()
            
            # Record failed generation (don't count against limit)
            limits = GenerationLimits(project.user)
            limits.record_generation(
                gpu_time_seconds=time.time() - start_time,
                success=False
            )
        except:
            pass
        
        # Retry on transient errors
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)
        
        raise exc

