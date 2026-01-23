"""
Enhanced AI Engine for FixPix
Professional-grade image processing using OpenCV
"""

import cv2
import numpy as np
import os
from django.conf import settings

try:
    from rembg import remove
except ImportError:
    remove = None

from PIL import Image
import io


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

        if file_bytes is None:
             raise ValueError("Could not read image source")

        # Convert bytes to numpy array
        nparr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Could not decode image data")
            
        return img

    @staticmethod
    def _save_result(image, original_path, suffix, return_path=True):
        """
        Helper to save processed image using Django Storage API.
        """
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        
        if not return_path:
            return image
            
        # Create output filename
        # original_path might be full absolute path or relative.
        # We want to base the new name on the basename.
        filename = os.path.basename(original_path)
        name, ext = os.path.splitext(filename)
        new_filename = f"{name}_{suffix}{ext}"
        
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
    def colorize_image(image_input, return_path=True, ref_path=""):
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
        
        return AIEngine._save_result(final, ref_path, 'colorized', return_path)

    @staticmethod
    def adjust_image(image_input, brightness=1.0, contrast=1.0, saturation=1.0, return_path=True, ref_path=""):
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
            
        return AIEngine._save_result(adjusted, ref_path, 'adjusted', return_path)

    @staticmethod
    def remove_scratches(image_input, strength=50, return_path=True, ref_path=""):
        """
        Enhanced scratch removal with multi-pass denoising.
        Uses bilateral filter to preserve edges while removing noise.
        
        Args:
            strength: 0-100, higher = more aggressive denoising
        """
        img = AIEngine._read_image(image_input)
        
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
        
        return AIEngine._save_result(denoised, ref_path, 'restored', return_path)

    @staticmethod
    def restore_faces(image_input, return_path=True, ref_path=""):
        """
        Enhanced face restoration with unsharp mask and local contrast.
        Better than simple sharpening.
        """
        img = AIEngine._read_image(image_input)
        
        # 1. Apply CLAHE for local contrast enhancement
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        
        # 2. Unsharp mask for sharpening (better than simple kernel)
        gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
        sharpened = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
        
        # 3. Light denoising to smooth skin while keeping features
        result = cv2.bilateralFilter(sharpened, d=5, sigmaColor=50, sigmaSpace=50)
        
        return AIEngine._save_result(result, ref_path, 'face_restored', return_path)

    @staticmethod
    def remove_background(image_input, return_path=True, ref_path=""):
        """Remove background using rembg or improved GrabCut fallback."""
        img_array = AIEngine._read_image(image_input)
        
        # Try rembg first (best quality)
        if remove is not None:
            try:
                success, encoded_img = cv2.imencode(".png", img_array)
                if success:
                    input_bytes = encoded_img.tobytes()
                    output_bytes = remove(input_bytes)
                    
                    nparr = np.frombuffer(output_bytes, np.uint8)
                    img_nobg = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
                    return AIEngine._save_result(img_nobg, ref_path, 'nobg', return_path)
            except Exception as e:
                print(f"rembg failed: {e}")

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
        
        return AIEngine._save_result(result_rgba, ref_path, 'nobg_grabcut', return_path)

    @staticmethod
    def auto_enhance(image_input, return_path=True, ref_path=""):
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
        
        # 2. CLAHE on L channel
        lab = cv2.cvtColor(balanced, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        final = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        
        # 3. Subtle saturation boost
        hsv = cv2.cvtColor(final, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.1, 0, 255)
        final = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        
        return AIEngine._save_result(final, ref_path, 'auto_enhanced', return_path)

    @staticmethod
    def inpaint_object(image_input, mask_input, return_path=True, ref_path=""):
        """Object removal using inpainting with mask."""
        img = AIEngine._read_image(image_input)
        
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
        
        return AIEngine._save_result(inpainted, ref_path, 'inpainted', return_path)

    @staticmethod
    def upscale_image(image_input, scale=2, return_path=True, ref_path=""):
        """
        Enhanced upscaling with:
        - High quality Lanczos interpolation
        - Adaptive sharpening
        - Noise reduction
        """
        img = AIEngine._read_image(image_input)
        if scale not in [2, 4]: 
            scale = 2
            
        h, w = img.shape[:2]
        new_size = (w * scale, h * scale)
        
        # 1. Upscale with Lanczos (best quality interpolation)
        upscaled = cv2.resize(img, new_size, interpolation=cv2.INTER_LANCZOS4)
        
        # 2. Light denoising to reduce interpolation artifacts
        upscaled = cv2.bilateralFilter(upscaled, d=5, sigmaColor=30, sigmaSpace=30)
        
        # 3. Adaptive sharpening (unsharp mask)
        gaussian = cv2.GaussianBlur(upscaled, (0, 0), 1.5)
        sharpened = cv2.addWeighted(upscaled, 1.4, gaussian, -0.4, 0)
        
        return AIEngine._save_result(sharpened, ref_path, f'upscaled_{scale}x', return_path)

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
