"""
Enhanced AI Engine for FixPix
Professional-grade image processing using OpenCV
"""

import cv2
import numpy as np
import os
from django.conf import settings
from .utils.watermark import apply_watermark

try:
    from rembg import remove
except ImportError:
    remove = None

from PIL import Image
import io
import logging

logger = logging.getLogger(__name__)

from .services.photoroom_service import PhotoroomService
from .services.pixian_service import PixianService

# Import EditHistory here to avoid circular imports if needed, 
# or use local imports in methods.


class AIEngine:
    
    # ============== HELPER METHODS ==============
    
    @staticmethod
    def _read_image(source):
        """
        Helper to ensure we have a CV2 image (numpy array).
        Supports: numpy array, path string (via Storage), or file-like object.
        """
        if isinstance(source, np.ndarray):
            return source

        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile

        file_bytes = None

        if isinstance(source, str):
            # If absolute path, try to make relative to MEDIA_ROOT for storage
            # But currently original_image IS a storage path (relative) usually. 
            # If it's absolute local path, we might need to be careful.
            # Ideally source is the relative path from DB model.
            
            # Try reading from storage
            if default_storage.exists(source):
                try:
                    with default_storage.open(source, 'rb') as f:
                        file_bytes = f.read()
                except Exception:
                    # Fallback for local dev absolute paths if not in storage context
                     if os.path.exists(source):
                        with open(source, 'rb') as f:
                            file_bytes = f.read()
            elif os.path.exists(source):
                 with open(source, 'rb') as f:
                    file_bytes = f.read()
            else:
                raise ValueError(f"Could not read image from {source}")

        elif hasattr(source, 'read'):
            file_bytes = source.read()
        elif isinstance(source, (bytes, bytearray)):
            file_bytes = source

        if file_bytes is None:
             raise ValueError("Could not read image source")

        # Convert bytes to numpy array
        nparr = np.frombuffer(file_bytes, np.uint8)
        # Use IMREAD_UNCHANGED to preserve Alpha channel if present (PNG/WebP)
        img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
        
        if img is None:
            raise ValueError("Could not decode image data")
            
        return img

    @staticmethod
    def _save_result(image, original_path, suffix, return_path=True, plan_name=None):
        """
        Helper to save processed image using Django Storage API.
        """
        if image is not None and plan_name == 'free':
            try:
                # Apply watermark for free users
                image = apply_watermark(image)
            except Exception as e:
                logger.error(f"Watermarking failed: {e}")
                
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        
        if not return_path:
            return image
            
        # Create output filename with unique fingerprint to prevent caching
        import time
        timestamp = int(time.time())
        filename = os.path.basename(original_path)
        name, ext = os.path.splitext(filename)
        
        # Fallback if no extension provided
        if not ext:
            ext = '.png'
            
        new_filename = f"{name}_{suffix}_{timestamp}{ext}"
        
        # Encode image to bytes
        success, encoded_img = cv2.imencode(ext, image)
        if not success:
             raise ValueError("Could not encode image for saving")
             
        content = ContentFile(encoded_img.tobytes())
        
        # Save key relative to MEDIA_ROOT (e.g. 'processed/foo_edited.jpg')
        save_path = os.path.join('processed', new_filename)
        
        # If file exists, storage.save usually appends random string.
        # We delete existing logic or let it happen.
        # Let's check if we want to overwrite or specific new name.
        # Django storage handles naming collision.
        
        saved_path = default_storage.save(save_path, content)
        
        return saved_path

    # ============== ENHANCED PROCESSING METHODS ==============

    @staticmethod
    def colorize_image(image_input, return_path=True, ref_path="", plan_name=None):
        """
        Apply enhanced vintage colorization with proper sepia toning.
        Much better than simple colormap approach.
        """
        img = AIEngine._read_image(image_input)
        
        # Convert to grayscale if needed
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img
        
        # Convert back to BGR for processing
        img_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        
        # Apply sepia transform matrix (classic sepia tone) - Corrected for BGR
        sepia_kernel = np.array([
            [0.131, 0.534, 0.272],
            [0.168, 0.686, 0.349],
            [0.189, 0.769, 0.393]
        ])
        sepia = cv2.transform(img_bgr, sepia_kernel)
        sepia = np.clip(sepia, 0, 255).astype(np.uint8)
        
        # Enhance contrast for vintage look
        lab = cv2.cvtColor(sepia, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        final = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        
        # Add subtle warm tint
        final = final.astype(np.float32)
        final[:, :, 2] = np.clip(final[:, :, 2] * 1.05, 0, 255)  # Slight red boost
        final = final.astype(np.uint8)
        
        return AIEngine._save_result(final, ref_path, 'colorized', return_path, plan_name=plan_name)

    @staticmethod
    def adjust_image(image_input, brightness=1.0, contrast=1.0, saturation=1.0, return_path=True, ref_path="", plan_name=None):
        """Adjust brightness, contrast, and saturation."""
        img = AIEngine._read_image(image_input)

        # 1. Brightness and Contrast
        beta = (brightness - 1.0) * 100
        adjusted = cv2.convertScaleAbs(img, alpha=contrast, beta=beta)
        
        # 2. Saturation
        if saturation != 1.0:
            hsv = cv2.cvtColor(adjusted, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = hsv[:, :, 1] * saturation
            hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
            adjusted = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
            
        return AIEngine._save_result(adjusted, ref_path, 'adjusted', return_path, plan_name=plan_name)

    @staticmethod
    def remove_scratches(image_input, strength=50, return_path=True, ref_path="", plan_name=None):
        """
        Enhanced scratch removal with multi-pass denoising.
        Uses bilateral filter to preserve edges while removing noise.
        
        Args:
            strength: 0-100, higher = more aggressive denoising
        """
        img = AIEngine._read_image(image_input)
        
        # --- PREMIUM: Stability AI Restoration ---
        if getattr(settings, 'STABILITY_API_KEYS', []):
            try:
                from .services.stability_service import StabilityService
                stability = StabilityService()
                # Encode to bytes for Stability
                _, buffer = cv2.imencode('.png', img)
                img_bytes = buffer.tobytes()
                
                logger.info("AIEngine: Using Stability AI for premium scratch removal/restoration")
                # Use a specialized restoration prompt and low strength to preserve identity
                restored_bytes = stability.edit_image(
                    img_bytes, 
                    prompt="professional heritage photo restoration, remove all scratches and dust, high resolution, clean background, sharp details, cinematic lighting, 8k professional retouch",
                    strength=0.35,
                    cfg_scale=8.5
                )
                
                if restored_bytes:
                    # Convert back to numpy
                    nparr = np.frombuffer(restored_bytes, np.uint8)
                    restored_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if restored_img is not None:
                        return AIEngine._save_result(restored_img, ref_path, 'restored_premium', return_path, plan_name=plan_name)
            except Exception as e:
                logger.error(f"AIEngine: Stability Restoration failed, falling back: {e}")

        # --- FALLBACK: OpenCV Restoration ---
        # Normalize strength to algorithm parameters
        h_value = max(3, min(15, int(strength / 10)))  # 3-15 range
        
        # Pass 1: Non-local means denoising (best for noise)
        denoised = cv2.fastNlMeansDenoisingColored(
            img, None, 
            h=h_value,           # Luminance noise
            hColor=h_value,      # Color noise (correct param name for OpenCV 4.x)
            templateWindowSize=7, 
            searchWindowSize=21
        )
        
        # Pass 2: Bilateral filter for edge preservation
        # This smooths while keeping edges sharp
        if strength > 30:
            denoised = cv2.bilateralFilter(denoised, d=9, sigmaColor=75, sigmaSpace=75)
        
        # Pass 3: Light sharpening to restore detail
        if strength > 20:
            gaussian = cv2.GaussianBlur(denoised, (0, 0), 1.0)
            denoised = cv2.addWeighted(denoised, 1.2, gaussian, -0.2, 0)
        
        return AIEngine._save_result(denoised, ref_path, 'restored', return_path, plan_name=plan_name)

    @staticmethod
    def restore_faces(image_input, return_path=True, ref_path="", fidelity=0.5, preserve_skin_tone=False, plan_name=None):
        """
        AI Face Restoration using GFPGAN.
        High-fidelity restoration that reconstructs facial features.
        """
        img = AIEngine._read_image(image_input)
        
        try:
            from .gfpgan_service import GFPGANService
            service = GFPGANService()
            result = service.restore(img, fidelity=fidelity)
        except Exception as e:
            print(f"GFPGAN Service failed: {e}. Falling back to OpenCV.")
            # Fallback: CLAHE + Unsharp Mask
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            l = clahe.apply(l)
            enhanced = cv2.merge([l, a, b])
            enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
            gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
            result = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
            
        if preserve_skin_tone:
            # Revert color shifts by keeping the original Chrominance (A,B) 
            # and only using the high-res Luminance (L) from the restored image.
            
            # Ensure sizes match exactly (AI might resize slightly)
            if img.shape[:2] != result.shape[:2]:
                img_resized = cv2.resize(img, (result.shape[1], result.shape[0]), interpolation=cv2.INTER_LANCZOS4)
            else:
                img_resized = img

            # Convert both to LAB color space
            orig_lab = cv2.cvtColor(img_resized, cv2.COLOR_BGR2LAB)
            restored_lab = cv2.cvtColor(result, cv2.COLOR_BGR2LAB)
            
            # Extract Luminance from restored, Chrominance from original
            l_restored, a_restored, b_restored = cv2.split(restored_lab)
            l_orig, a_orig, b_orig = cv2.split(orig_lab)
            
            # Merge restored luminance with original colors
            merged_lab = cv2.merge([l_restored, a_orig, b_orig])
            
            # Convert back to BGR
            result = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
        
        return AIEngine._save_result(result, ref_path, 'face_restored', return_path, plan_name=plan_name)

    @staticmethod
    def remove_background(image_input, return_path=True, ref_path="", plan_name=None):
        """
        Remove background using professional Photoroom AI, 
        with fallback to local rembg and GrabCut.
        """
        img_array = AIEngine._read_image(image_input)
        
        # 1. PREMIUM: Photoroom AI (Highest Accuracy)
        try:
            # Encode to bytes for API
            _, buffer = cv2.imencode('.jpg', img_array)
            img_bytes = buffer.tobytes()
            
            photoroom = PhotoroomService()
            result_bytes = photoroom.remove_background(img_bytes)
            
            if result_bytes:
                logger.info("AIEngine: Successfully removed background via Photoroom AI")
                nparr = np.frombuffer(result_bytes, np.uint8)
                img_nobg = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                if img_nobg is not None:
                    return AIEngine._save_result(img_nobg, ref_path, 'nobg_photoroom', return_path, plan_name=plan_name)
        except Exception as e:
            logger.warn(f"AIEngine: Photoroom API failed, falling back: {e}")

        # 2. PREMIUM FALLBACK: Pixian AI (High Fidelity)
        try:
            _, buffer = cv2.imencode('.jpg', img_array)
            img_bytes = buffer.tobytes()
            
            pixian = PixianService()
            result_bytes = pixian.remove_background(img_bytes)
            
            if result_bytes:
                logger.info("AIEngine: Successfully removed background via Pixian AI fallback")
                nparr = np.frombuffer(result_bytes, np.uint8)
                img_nobg = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                if img_nobg is not None:
                    return AIEngine._save_result(img_nobg, ref_path, 'nobg_pixian', return_path, plan_name=plan_name)
        except Exception as e:
            logger.warn(f"AIEngine: Pixian API failed: {e}")

        # 3. LOCAL AI: rembg (Fast & Free)
        if remove is not None:
            try:
                success, encoded_img = cv2.imencode(".png", img_array)
                if success:
                    input_bytes = encoded_img.tobytes()
                    output_bytes = remove(input_bytes)
                    
                    nparr = np.frombuffer(output_bytes, np.uint8)
                    img_nobg = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                    if img_nobg is not None:
                        logger.info("AIEngine: Successfully removed background via local rembg")
                        return AIEngine._save_result(img_nobg, ref_path, 'nobg_rembg', return_path, plan_name=plan_name)
            except Exception as e:
                logger.error(f"AIEngine: rembg failed: {e}")

        # Improved GrabCut fallback
        print("Using improved GrabCut")
        h, w = img_array.shape[:2]
        
        # Better rectangle - slightly inset from edges
        margin_x = int(w * 0.02)
        margin_y = int(h * 0.02)
        rect = (margin_x, margin_y, w - margin_x * 2, h - margin_y * 2)
        
        mask = np.zeros((h, w), np.uint8)
        bgdModel = np.zeros((1, 65), np.float64)
        fgdModel = np.zeros((1, 65), np.float64)
        
        # More iterations for better quality
        cv2.grabCut(img_array, mask, rect, bgdModel, fgdModel, 5, cv2.GC_INIT_WITH_RECT)
        
        # Create binary mask
        mask2 = np.where((mask == 2) | (mask == 0), 0, 1).astype('uint8')
        
        # Morphological cleanup for smoother edges
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask2 = cv2.morphologyEx(mask2, cv2.MORPH_CLOSE, kernel)
        mask2 = cv2.morphologyEx(mask2, cv2.MORPH_OPEN, kernel)
        
        # Feather edges for natural look
        mask_float = mask2.astype(np.float32)
        mask_float = cv2.GaussianBlur(mask_float, (5, 5), 0)
        
        # Apply to image
        result = img_array.copy()
        for i in range(3):
            result[:, :, i] = (result[:, :, i] * mask_float).astype(np.uint8)
        
        # Add alpha channel
        alpha = (mask_float * 255).astype(np.uint8)
        b, g, r = cv2.split(result)
        result_rgba = cv2.merge([b, g, r, alpha])
        
        return AIEngine._save_result(result_rgba, ref_path, 'nobg_grabcut', return_path, plan_name=plan_name)

    @staticmethod
    def auto_enhance(image_input, return_path=True, ref_path="", plan_name=None):
        """
        Enhanced auto-enhancement with:
        - CLAHE for contrast
        - Auto white balance
        - Subtle saturation boost
        """
        img = AIEngine._read_image(image_input)
        
        # 1. Auto White Balance (Gray World algorithm)
        img_float = img.astype(np.float32)
        avg_b = np.mean(img_float[:, :, 0])
        avg_g = np.mean(img_float[:, :, 1])
        avg_r = np.mean(img_float[:, :, 2])
        avg_gray = (avg_b + avg_g + avg_r) / 3
        
        img_float[:, :, 0] = np.clip(img_float[:, :, 0] * (avg_gray / avg_b), 0, 255)
        img_float[:, :, 1] = np.clip(img_float[:, :, 1] * (avg_gray / avg_g), 0, 255)
        img_float[:, :, 2] = np.clip(img_float[:, :, 2] * (avg_gray / avg_r), 0, 255)
        balanced = img_float.astype(np.uint8)
        
        # 2. Advanced CLAHE on L channel
        lab = cv2.cvtColor(balanced, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        # Higher clip limit for more 'pop' without over-exposure
        clahe = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(12, 12))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        final = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        
        # 3. Vivid Saturation Boost (Selective)
        hsv = cv2.cvtColor(final, cv2.COLOR_BGR2HSV).astype(np.float32)
        # Boost saturation while preserving skin tones
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.15, 0, 255)
        final = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        
        return AIEngine._save_result(final, ref_path, 'auto_enhanced', return_path, plan_name=plan_name)

    @staticmethod
    def inpaint_object(image_input, mask_input, return_path=True, ref_path="", prompt="", plan_name=None):
        """Object removal using inpainting with mask."""
        img = AIEngine._read_image(image_input)
        
        # --- LOCAL: LaMa Inpaint (Primary) ---
        try:
            from .services.LaMaService import get_lama
            lama = get_lama()
            
            # Prepare Mask
            if isinstance(mask_input, str):
                mask_src = cv2.imread(mask_input, cv2.IMREAD_UNCHANGED)
            else:
                mask_src = mask_input
                
            if mask_src is not None:
                # 1. Standardize mask
                if len(mask_src.shape) == 3 and mask_src.shape[2] == 4:
                    mask_gray = mask_src[:, :, 3]
                elif len(mask_src.shape) == 3:
                    mask_gray = cv2.cvtColor(mask_src, cv2.COLOR_BGR2GRAY)
                else:
                    mask_gray = mask_src
                
                # 2. Refine mask (binary + dilation)
                _, mask_gray = cv2.threshold(mask_gray, 10, 255, cv2.THRESH_BINARY)
                kernel_size = max(5, int(min(mask_gray.shape) * 0.02))
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
                mask_gray = cv2.dilate(mask_gray, kernel, iterations=2)
                
                logger.info("AIEngine: Attempting local LaMa inpainting")
                result_np = lama.inpaint(img, mask_gray)
                
                if result_np is not None:
                    return AIEngine._save_result(result_np, ref_path, 'inpainted_lama', return_path, plan_name=plan_name)
                    
        except Exception as e:
            logger.error(f"AIEngine: LaMa Inpaint failed, trying fallback: {e}")

        # --- PREMIUM: Stability AI Inpaint (Fallback 1) ---
        if getattr(settings, 'STABILITY_API_KEYS', []):
            try:
                from .services.stability_service import StabilityService
                stability = StabilityService()
                
                # Prepare Image Bytes
                _, img_buffer = cv2.imencode('.png', img)
                img_bytes = img_buffer.tobytes()
                
                # Prepare Mask Bytes (already refined above)
                if 'mask_gray' in locals():
                    _, mask_buffer = cv2.imencode('.png', mask_gray)
                    mask_bytes = mask_buffer.tobytes()
                    
                    logger.info("AIEngine: Using Stability AI for premium inpainting fallback")
                    result_bytes = stability.inpaint_image(img_bytes, mask_bytes, prompt=prompt)
                    
                    if result_bytes:
                        nparr = np.frombuffer(result_bytes, np.uint8)
                        inpainted_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                        if inpainted_img is not None:
                            return AIEngine._save_result(inpainted_img, ref_path, 'inpainted_premium', return_path, plan_name=plan_name)
            except Exception as e:
                logger.error(f"AIEngine: Stability Inpaint failed, falling back: {e}")

        # --- FALLBACK: OpenCV Inpaint ---
        if isinstance(mask_input, str):
            mask_src = cv2.imread(mask_input, cv2.IMREAD_UNCHANGED)
        else:
            mask_src = mask_input
            
        if mask_src is None: 
            raise ValueError("Invalid mask")
            
        # Handle different mask formats
        if len(mask_src.shape) == 3 and mask_src.shape[2] == 4:
            # BGRA - use Alpha channel
            mask = mask_src[:, :, 3]
        elif len(mask_src.shape) == 3:
            # BGR - convert to grayscale
            mask = cv2.cvtColor(mask_src, cv2.COLOR_BGR2GRAY)
        else:
            mask = mask_src

        # Resize mask to match image
        if img.shape[:2] != mask.shape[:2]:
            mask = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)

        # Threshold to binary
        _, mask = cv2.threshold(mask, 10, 255, cv2.THRESH_BINARY)
        
        # Dilate mask for better edge coverage
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=2)

        # Inpaint using Telea method (better for larger areas)
        inpainted = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)
        
        return AIEngine._save_result(inpainted, ref_path, 'inpainted', return_path, plan_name=plan_name)

    @staticmethod
    def _detect_faces(img):
        """
        Detects if human faces are present in the image.
        Uses a high-performance grayscale pass with Haar Cascades.
        """
        try:
            # 1. Prepare image for detection (smaller + grayscale is faster)
            scale_limit = 800
            h, w = img.shape[:2]
            if w > scale_limit or h > scale_limit:
                r = scale_limit / max(h, w)
                gray_img = cv2.resize(img, (int(w * r), int(h * r)))
            else:
                gray_img = img.copy()

            if len(gray_img.shape) == 3:
                gray = cv2.cvtColor(gray_img, cv2.COLOR_BGR2GRAY)
            else:
                gray = gray_img

            # 2. Use standard Haar Cascade
            face_cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            face_cascade = cv2.CascadeClassifier(face_cascade_path)
            
            # Detect faces
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)
            
            return len(faces) > 0
        except Exception as e:
            logger.warning(f"AIEngine: Face detection utility failed: {e}")
            return False

    @staticmethod
    def upscale_image(image_input, scale=2, return_path=True, ref_path="", plan_name=None):
        """
        Smart Super Resolution:
        - Detects Persons/Faces -> Routes to GFPGAN (Hidelity Portrait UPSC)
        - Detects Scenery/Nature -> Routes to Stability AI (Hidelity Nature UPSC)
        """
        img = AIEngine._read_image(image_input)
        h, w = img.shape[:2]

        # ─── OPTIMIZATION: Speed Check ───
        # AI upscaling huge images is slow. If already > 1536px, use high-quality local upscale.
        if (w * h) > (1600 * 1600):
            logger.info("AIEngine: Image is already high-res. Using local high-quality upscaler for speed.")
            new_size = (int(w * scale), int(h * scale))
            upscaled = cv2.resize(img, new_size, interpolation=cv2.INTER_LANCZOS4)
            upscaled = cv2.bilateralFilter(upscaled, d=5, sigmaColor=30, sigmaSpace=30)
            return AIEngine._save_result(upscaled, ref_path, f'upscaled_fast_{scale}x', return_path, plan_name=plan_name)

        # ─── SMART ROUTING ───
        has_faces = AIEngine._detect_faces(img)
        
        # CHOICE 1: PORTRAIT/PEOPLE (GFPGAN Path)
        if has_faces:
            logger.info(f"AIEngine: Smart Routing -> Person detected. Using GFPGAN for high-fidelity facial upscaling ({scale}x)")
            try:
                from .gfpgan_service import GFPGANService
                service = GFPGANService()
                # Run GFPGAN with requested upscale
                result_img = service.restore(img, fidelity=0.5, upscale=scale)
                if result_img is not None:
                    return AIEngine._save_result(result_img, ref_path, f'upscaled_face_{scale}x', return_path, plan_name=plan_name)
            except Exception as e:
                logger.error(f"AIEngine: Smart GFPGAN failed: {e}")

        # CHOICE 2: GENERAL/NATURE (Stability Path)
        if getattr(settings, 'STABILITY_API_KEYS', []):
            try:
                from .services.stability_service import StabilityService
                stability = StabilityService()
                
                # Encode Image for Stability
                _, encoded_img = cv2.imencode('.png', img)
                img_bytes = encoded_img.tobytes()
                
                logger.info(f"AIEngine: Smart Routing -> Scenery/Object detected. Using Stability AI for nature-optimized upscaling")
                # Conservative is synchronous and very high fidelity
                upscaled_bytes = stability.conservative_upscale(img_bytes)
                
                if upscaled_bytes:
                    nparr = np.frombuffer(upscaled_bytes, np.uint8)
                    upscaled_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if upscaled_img is not None:
                        # Stability usually upscales to 4K. Resize if user specifically wants a different scale.
                        # But typically 4K is what they want for "Super Res".
                        return AIEngine._save_result(upscaled_img, ref_path, f'upscaled_nature_{scale}x', return_path, plan_name=plan_name)
            except Exception as e:
                logger.error(f"AIEngine: Stability Upscale failed, falling back: {e}")

        # --- FINAL FALLBACK: OpenCV Lanczos ---
        if scale not in [2, 4, 8]: 
            scale = 2
            
        new_size = (w * scale, h * scale)
        upscaled = cv2.resize(img, new_size, interpolation=cv2.INTER_LANCZOS4)
        upscaled = cv2.bilateralFilter(upscaled, d=5, sigmaColor=30, sigmaSpace=30)
        gaussian = cv2.GaussianBlur(upscaled, (0, 0), 1.5)
        sharpened = cv2.addWeighted(upscaled, 1.4, gaussian, -0.4, 0)
        
        return AIEngine._save_result(sharpened, ref_path, f'upscaled_lanczos_{scale}x', return_path, plan_name=plan_name)

    # ============== NEW METHODS ==============

    @staticmethod
    def denoise_advanced(image_input, strength=50, return_path=True, ref_path=""):
        """
        Advanced denoising with configurable strength.
        
        Args:
            strength: 0-100 (0=minimal, 100=maximum denoising)
        """
        img = AIEngine._read_image(image_input)
        
        # Map strength to parameters
        h_luminance = max(3, int(strength / 5))  # 3-20
        h_color = max(3, int(strength / 6))  # 3-16
        
        # Non-local means denoising
        denoised = cv2.fastNlMeansDenoisingColored(
            img, None,
            h=h_luminance,
            hColor=h_color,      # Correct param name for OpenCV 4.x
            templateWindowSize=7,
            searchWindowSize=21
        )
        
        # Additional bilateral for higher strengths
        if strength > 50:
            d = 9 if strength > 75 else 7
            denoised = cv2.bilateralFilter(denoised, d=d, sigmaColor=75, sigmaSpace=75)
        
        return AIEngine._save_result(denoised, ref_path, 'denoised', return_path)

    @staticmethod
    def correct_white_balance(image_input, return_path=True, ref_path=""):
        """
        Auto white balance using Gray World algorithm.
        Corrects color casts in photos.
        """
        img = AIEngine._read_image(image_input)
        
        img_float = img.astype(np.float32)
        
        # Calculate channel averages
        avg_b = np.mean(img_float[:, :, 0])
        avg_g = np.mean(img_float[:, :, 1])
        avg_r = np.mean(img_float[:, :, 2])
        
        # Gray world assumption: average should be gray
        avg_gray = (avg_b + avg_g + avg_r) / 3
        
        # Scale channels
        if avg_b > 0:
            img_float[:, :, 0] = np.clip(img_float[:, :, 0] * (avg_gray / avg_b), 0, 255)
        if avg_g > 0:
            img_float[:, :, 1] = np.clip(img_float[:, :, 1] * (avg_gray / avg_g), 0, 255)
        if avg_r > 0:
            img_float[:, :, 2] = np.clip(img_float[:, :, 2] * (avg_gray / avg_r), 0, 255)
        
        result = img_float.astype(np.uint8)
        return AIEngine._save_result(result, ref_path, 'wb_corrected', return_path)

    @staticmethod
    def apply_filter_preset(image_input, preset_name, return_path=True, ref_path=""):
        """
        Apply a professional filter preset.
        
        Available presets: vintage, cinematic, warm, cool, bw_classic, bw_noir, fade, vivid
        """
        from .ai_presets import apply_preset
        
        img = AIEngine._read_image(image_input)
        result = apply_preset(img, preset_name)
        return AIEngine._save_result(result, ref_path, f'filter_{preset_name}', return_path)

    # ============== ADVANCED AI FEATURES ==============

    @staticmethod
    def generative_inpaint(image_input, mask_input, return_path=True, ref_path=""):
        """
        Advanced inpainting using OpenCV's TELEA and NS algorithms.
        Fills masked areas with content-aware fill.
        
        Args:
            image_input: Original image
            mask_input: Binary mask (white = area to fill)
        """
        img = AIEngine._read_image(image_input)
        
        # Read or process mask
        if isinstance(mask_input, str):
            mask = cv2.imread(mask_input, cv2.IMREAD_GRAYSCALE)
        elif isinstance(mask_input, np.ndarray):
            if len(mask_input.shape) == 3:
                mask = cv2.cvtColor(mask_input, cv2.COLOR_BGR2GRAY)
            else:
                mask = mask_input
        else:
            raise ValueError("Invalid mask input")
        
        # Ensure mask is same size as image
        if mask.shape[:2] != img.shape[:2]:
            mask = cv2.resize(mask, (img.shape[1], img.shape[0]))
        
        # Threshold mask to binary
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
        
        # Dilate mask slightly for better blending
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
        
        # Apply inpainting (TELEA is generally better for textures)
        # Radius 5-7 works well for most cases
        inpainted = cv2.inpaint(img, mask, inpaintRadius=7, flags=cv2.INPAINT_TELEA)
        
        # Optional: blend edges for smoother transition
        # Create soft mask for blending
        soft_mask = cv2.GaussianBlur(mask.astype(np.float32) / 255, (15, 15), 0)
        soft_mask = np.expand_dims(soft_mask, axis=2)
        
        # Blend original edges with inpainted
        result = (inpainted * soft_mask + img * (1 - soft_mask)).astype(np.uint8)
        
        return AIEngine._save_result(result, ref_path, 'inpainted', return_path)

    @staticmethod
    def replace_background(image_input, bg_type='blur', bg_color=(255, 255, 255), blur_strength=25, return_path=True, ref_path=""):
        """
        Replace or modify background.
        
        Args:
            bg_type: 'blur', 'solid', 'transparent', or 'gradient'
            bg_color: RGB tuple for solid background
            blur_strength: Blur kernel size for blur mode
        """
        img = AIEngine._read_image(image_input)
        
        # Get foreground mask using rembg or GrabCut
        if remove is not None:
            try:
                success, encoded_img = cv2.imencode(".png", img)
                if success:
                    input_bytes = encoded_img.tobytes()
                    output_bytes = remove(input_bytes)
                    nparr = np.frombuffer(output_bytes, np.uint8)
                    img_rgba = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                    
                    if img_rgba.shape[2] == 4:
                        # Extract alpha channel as mask
                        fg_mask = img_rgba[:, :, 3]
                        foreground = img_rgba[:, :, :3]
                    else:
                        fg_mask = np.ones(img.shape[:2], dtype=np.uint8) * 255
                        foreground = img
            except Exception:
                fg_mask = np.ones(img.shape[:2], dtype=np.uint8) * 255
                foreground = img
        else:
            # Simple center-based GrabCut fallback
            h, w = img.shape[:2]
            mask = np.zeros((h, w), np.uint8)
            rect = (int(w*0.1), int(h*0.1), int(w*0.8), int(h*0.8))
            bgd_model = np.zeros((1, 65), np.float64)
            fgd_model = np.zeros((1, 65), np.float64)
            cv2.grabCut(img, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
            fg_mask = np.where((mask == 2) | (mask == 0), 0, 255).astype('uint8')
            foreground = img
        
        # Normalize mask
        fg_mask_float = fg_mask.astype(np.float32) / 255.0
        fg_mask_3ch = np.expand_dims(fg_mask_float, axis=2)
        
        # Create background based on type
        if bg_type == 'blur':
            # Blurred version of original (portrait mode effect)
            # Ensure blur_strength is odd
            blur_strength = blur_strength if blur_strength % 2 == 1 else blur_strength + 1
            background = cv2.GaussianBlur(img, (blur_strength, blur_strength), 0)
        elif bg_type == 'solid':
            # Solid color background
            background = np.full(img.shape, bg_color[::-1], dtype=np.uint8)  # BGR
        elif bg_type == 'gradient':
            # Gradient background (top to bottom)
            background = np.zeros(img.shape, dtype=np.uint8)
            h = img.shape[0]
            for y in range(h):
                ratio = y / h
                color = [int(bg_color[2] * (1 - ratio)), int(bg_color[1] * (1 - ratio) + 128 * ratio), int(bg_color[0] * (1 - ratio) + 255 * ratio)]
                background[y, :] = color
        else:  # transparent - return with alpha
            result = cv2.cvtColor(foreground, cv2.COLOR_BGR2BGRA)
            result[:, :, 3] = fg_mask
            return AIEngine._save_result(result, ref_path, 'bg_replaced', return_path)
        
        # Composite foreground over new background
        result = (foreground * fg_mask_3ch + background * (1 - fg_mask_3ch)).astype(np.uint8)
        
        return AIEngine._save_result(result, ref_path, 'bg_replaced', return_path)

    @staticmethod
    def detect_faces(image_input):
        """
        Detect faces and return coordinates.
        """
        img = AIEngine._read_image(image_input)
        
        # Load face cascade
        face_cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        face_cascade = cv2.CascadeClassifier(face_cascade_path)
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, 
            scaleFactor=1.1, 
            minNeighbors=5, 
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE
        )
        
        # Return as list of dicts with original image dimensions for reference
        results = []
        for (x, y, w, h) in faces:
            results.append({
                'x': int(x),
                'y': int(y),
                'w': int(w),
                'h': int(h)
            })
            
        return {
            'faces': results,
            'imageSize': {
                'width': img.shape[1],
                'height': img.shape[0]
            }
        }

    @staticmethod
    def enhance_face_details(image_input, eye_enhance=True, skin_smooth=True, sharpen_strength=1.2, return_path=True, ref_path=""):
        """
        Targeted face enhancement with eye brightening and skin smoothing.
        Uses Haar cascades for face detection.
        """
        img = AIEngine._read_image(image_input)
        result = img.copy()
        
        # Load face cascade
        face_cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        eye_cascade_path = cv2.data.haarcascades + 'haarcascade_eye.xml'
        
        face_cascade = cv2.CascadeClassifier(face_cascade_path)
        eye_cascade = cv2.CascadeClassifier(eye_cascade_path)
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        
        for (x, y, w, h) in faces:
            # Extract face region with margin
            margin = int(w * 0.1)
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(img.shape[1], x + w + margin)
            y2 = min(img.shape[0], y + h + margin)
            
            face_region = result[y1:y2, x1:x2].copy()
            
            # Skin smoothing with bilateral filter
            if skin_smooth:
                smooth = cv2.bilateralFilter(face_region, d=9, sigmaColor=75, sigmaSpace=75)
                # Blend to keep some texture
                face_region = cv2.addWeighted(face_region, 0.3, smooth, 0.7, 0)
            
            # Eye enhancement
            if eye_enhance:
                roi_gray = gray[y:y+h, x:x+w]
                eyes = eye_cascade.detectMultiScale(roi_gray)
                
                for (ex, ey, ew, eh) in eyes:
                    # Adjust coordinates for face_region
                    eye_x1 = max(0, ex + margin - margin)
                    eye_y1 = max(0, ey + margin - margin)
                    eye_x2 = min(face_region.shape[1], ex + ew + margin)
                    eye_y2 = min(face_region.shape[0], ey + eh + margin)
                    
                    eye_region = face_region[eye_y1:eye_y2, eye_x1:eye_x2]
                    if eye_region.size > 0:
                        # Increase brightness and contrast for eyes
                        eye_enhanced = cv2.convertScaleAbs(eye_region, alpha=1.1, beta=10)
                        face_region[eye_y1:eye_y2, eye_x1:eye_x2] = eye_enhanced
            
            # Apply sharpening to face
            if sharpen_strength > 1.0:
                gaussian = cv2.GaussianBlur(face_region, (0, 0), 2.0)
                face_region = cv2.addWeighted(face_region, sharpen_strength, gaussian, -(sharpen_strength - 1), 0)
            
            # Put enhanced face back
            result[y1:y2, x1:x2] = face_region
        
        return AIEngine._save_result(result, ref_path, 'face_enhanced', return_path)

    # ============== STICKER ENGINE (MIGRATED FROM NODE.JS) ==============

    @staticmethod
    def add_sticker_outline(image_array, outline_width=8, outline_color=(255, 255, 255)):
        """
        Professional-grade sticker outline effect using OpenCV dilation.
        Replicates the high-end 'Sharp' logic in Python.
        """
        # Ensure image has 4 channels
        if image_array.shape[2] == 3:
            # Add dummy alpha if missing
            b, g, r = cv2.split(image_array)
            a = np.ones(b.shape, dtype=np.uint8) * 255
            img = cv2.merge([b, g, r, a])
        else:
            img = image_array.copy()

        h, w = img.shape[:2]
        
        # 1. Extract Alpha mask
        alpha = img[:, :, 3]
        
        # 2. Dilate the alpha mask to create the outline silhouette
        # We use a circular kernel for smoother rounded corners
        kernel_size = outline_width * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        dilated_mask = cv2.dilate(alpha, kernel, iterations=1)
        
        # 3. Create the colored outline layer
        # Base color (BGR) + Alpha (dilated_mask)
        outline_bgr = np.full((h, w, 3), outline_color[::-1], dtype=np.uint8)
        outline_layer = cv2.merge([outline_bgr[:,:,0], outline_bgr[:,:,1], outline_bgr[:,:,2], dilated_mask])
        
        # 4. Composite: Outline (bottom) + Original (top)
        # We use PIL for high-quality alpha blending
        from PIL import Image
        
        bg_pil = Image.fromarray(outline_layer, 'RGBA')
        fg_pil = Image.fromarray(img, 'RGBA')
        
        # Paste foreground onto background using its own alpha as mask
        bg_pil.paste(fg_pil, (0, 0), fg_pil)
        
        return np.array(bg_pil)

    @staticmethod
    def generate_sticker(image_input, outline_width=10, outline_color=(255, 255, 255), return_path=True, ref_path=""):
        """
        Full pipeline: Remove BG (if needed) -> Add Outline -> Save.
        """
        img = AIEngine._read_image(image_input)
        
        # 1. If image is not transparent, attempt BG removal first
        if img.shape[2] == 3:
            logger.info("AIEngine: Input for sticker has no alpha. Recommending BG removal.")
            # We don't auto-remove here to avoid infinite loops, 
            # we expect the view to have called remove_background first.
            # But let's add a dummy alpha for now if it really is just BGR
            b, g, r = cv2.split(img)
            a = np.ones(b.shape, dtype=np.uint8) * 255
            img = cv2.merge([b, g, r, a])

        # 2. Add high-quality outline
        sticker_img = AIEngine.add_sticker_outline(img, outline_width, outline_color)
        
        return AIEngine._save_result(sticker_img, ref_path, 'sticker', return_path)

    @staticmethod
    def log_edit_history(user, tool, parameters, output_url):
        """
        Helper to log an AI action to the persistent database history.
        """
        if user and user.is_authenticated:
            try:
                from .models import EditHistory
                EditHistory.objects.create(
                    user=user,
                    tool=tool,
                    parameters=parameters,
                    output_url=output_url
                )
                logger.info(f"AIEngine: Logged {tool} to history for user {user.username}")
            except Exception as e:
                logger.error(f"AIEngine: Failed to log history: {e}")
