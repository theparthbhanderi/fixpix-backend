"""
GFPGAN Service for FixPix

Provides high-fidelity face restoration using TencentARC's GFPGAN model.
Supports MOCK_MODE for development without GPU or large weight files.
"""

import os
import time
import sys
import cv2
import numpy as np
import logging
import torchvision
from django.conf import settings
from django.core.files.storage import default_storage

# Fix for basicsr/torchvision incompatibility
# No module named 'torchvision.transforms.functional_tensor'
if not hasattr(torchvision.transforms, 'functional_tensor'):
    import torchvision.transforms.functional as tv_functional
    sys.modules['torchvision.transforms.functional_tensor'] = tv_functional

logger = logging.getLogger(__name__)

# Check if we're in mock mode
MOCK_MODE = os.environ.get('GFPGAN_MOCK', 'true').lower() == 'true'

# Model cache for warm keeping
_model_cache = {
    'restorer': None,
    'loaded': False,
    'loading': False
}

class GFPGANService:
    """
    GFPGAN Service for professional face restoration.
    Ref: https://github.com/TencentARC/GFPGAN
    """

    def __init__(self):
        self.mock_mode = MOCK_MODE
        self.device = 'cuda' if not self.mock_mode else 'cpu'

    @classmethod
    def load_model(cls, model_path=None):
        """
        Load GFPGAN model into memory.
        """
        global _model_cache

        if _model_cache['loaded'] or _model_cache['loading']:
            return

        if MOCK_MODE:
            logger.info("GFPGAN: Running in MOCK mode - no model loaded")
            _model_cache['loaded'] = True
            return

        _model_cache['loading'] = True
        logger.info("GFPGAN: Loading GFPGANv1.4 model...")

        try:
            from gfpgan import GFPGANer
            
            # Default model path in backend/models/
            if model_path is None:
                model_path = os.path.join(settings.BASE_DIR, 'models', 'GFPGANv1.4.pth')

            if not os.path.exists(model_path):
                logger.warning(f"GFPGAN: Model file not found at {model_path}. Falling back to automatic download or mock.")
                # GFPGANer usually handles automatic download if path is None or invalid
            
            _model_cache['restorer'] = GFPGANer(
                model_path=model_path,
                upscale=2,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=None # We handle upscaling separately in AIEngine
            )
            
            _model_cache['loaded'] = True
            logger.info("GFPGAN: Model loaded successfully")

        except Exception as e:
            logger.error(f"GFPGAN: Failed to load model: {e}")
            _model_cache['loading'] = False
            # Don't raise, allowing fallback to mock or OpenCV
        finally:
            _model_cache['loading'] = False

    def restore(self, img, fidelity=0.5, upscale=2):
        """
        Perform face restoration and upscaling on a CV2 image.
        
        Args:
            img: BGR numpy array
            fidelity: 0.0 to 1.0 (restoration strength/fidelity)
            upscale: Target upscale factor (e.g. 2, 4)
            
        Returns:
            Restored and upscaled BGR image
        """
        if self.mock_mode:
            return self._restore_mock(img, fidelity, upscale)
            
        return self._restore_real(img, fidelity, upscale)

    def _restore_mock(self, img, fidelity, upscale=2):
        """
        Mock restoration using high-quality sharpening and contrast.
        """
        logger.info(f"GFPGAN: [MOCK] Restoring faces with fidelity: {fidelity}, upscale: {upscale}x")
        
        # Simulate processing time
        time.sleep(0.4)
        
        # 1. Local Contrast Enhancement (CLAHE)
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0 + (fidelity * 2), tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        
        # 2. Unsharp Masking
        gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
        sharpened = cv2.addWeighted(enhanced, 1.5 + (fidelity * 0.5), gaussian, -0.5 - (fidelity * 0.5), 0)
        
        # 3. Bilateral Filter for skin smoothing
        smoothed = cv2.bilateralFilter(sharpened, d=5, sigmaColor=50, sigmaSpace=50)
        
        # 4. Upscale result
        if upscale != 1:
            h, w = smoothed.shape[:2]
            result = cv2.resize(smoothed, (w * upscale, h * upscale), interpolation=cv2.INTER_LANCZOS4)
        else:
            result = smoothed

        return result

    def _restore_real(self, img, fidelity, upscale=2):
        """
        Perform actual GFPGAN inference.
        """
        global _model_cache
        
        if not _model_cache['loaded']:
            self.load_model()
            
        if _model_cache['restorer'] is None:
            logger.warning("GFPGAN: Real restorer not available, falling back to mock")
            return self._restore_mock(img, fidelity, upscale)
            
        try:
            # GFPGANer.enhance returns: cropped_faces, restored_faces, restored_img
            _, _, restored_img = _model_cache['restorer'].enhance(
                img,
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
                weight=fidelity # fidelity maps to weight in GFPGAN
            )
            
            # Since GFPGANer is initialized with a fixed upscale in our load_model,
            # we may need to resize the final result if upscale doesn't match
            current_upscale = getattr(_model_cache['restorer'], 'upscale', 2)
            if upscale != current_upscale:
                logger.info(f"GFPGAN: Resizing output from {current_upscale}x to {upscale}x")
                h, w = restored_img.shape[:2]
                target_h, target_w = int(img.shape[0] * upscale), int(img.shape[1] * upscale)
                restored_img = cv2.resize(restored_img, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

            return restored_img
        except Exception as e:
            logger.error(f"GFPGAN: Inference failed: {e}")
            return self._restore_mock(img, fidelity, upscale)

# Helper to pre-load for workers
def warmup_gfpgan():
    if not MOCK_MODE:
        GFPGANService.load_model()
