"""
DeepFloyd IF Service for FixPix

Provides AI image generation using DeepFloyd IF text-to-image model.
Supports mock mode for development without GPU.

Usage:
    from api.deepfloyd_service import DeepFloydService
    
    service = DeepFloydService()
    result_path = service.generate(prompt="A cat", style="photorealistic")
"""

import os
import time
import random
import logging
from pathlib import Path
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

# Check if we're in mock mode (no GPU available)
MOCK_MODE = os.environ.get('DEEPFLOYD_MOCK', 'true').lower() == 'true'

# Model cache for warm keeping
_model_cache = {
    'stage_1': None,
    'stage_2': None,
    'stage_3': None,
    'loaded': False,
    'loading': False,
}


class DeepFloydService:
    """
    DeepFloyd IF text-to-image generation service.
    
    Implements a 3-stage cascade:
    1. IF-I-XL: Text-to-64x64 image
    2. IF-II-L: 64x64 to 256x256 upscaling
    3. Stable x4 Upscaler: 256x256 to 1024x1024
    
    In mock mode, returns placeholder images for testing.
    """
    
    # Style-specific prompt modifiers
    STYLE_MODIFIERS = {
        'photorealistic': 'photorealistic, highly detailed, 8k uhd, professional photography, ',
        'artistic': 'artistic, oil painting style, expressive brushstrokes, vibrant colors, ',
        'anime': 'anime style, studio ghibli, detailed anime art, vibrant, ',
    }
    
    # Negative prompt for quality improvement
    NEGATIVE_PROMPT = (
        'low quality, blurry, distorted, disfigured, bad anatomy, '
        'watermark, signature, text, ugly, duplicate, morbid, mutilated'
    )
    
    def __init__(self):
        self.mock_mode = MOCK_MODE
        self.device = 'cuda' if not self.mock_mode else 'cpu'
        
    @classmethod
    def load_models(cls):
        """
        Load DeepFloyd IF models into GPU memory.
        Call this during worker startup for warm-keeping.
        """
        global _model_cache
        
        if _model_cache['loaded'] or _model_cache['loading']:
            return
        
        if MOCK_MODE:
            logger.info("DeepFloyd: Running in MOCK mode - no models loaded")
            _model_cache['loaded'] = True
            return
        
        _model_cache['loading'] = True
        logger.info("DeepFloyd: Loading models into GPU memory...")
        
        try:
            from diffusers import DiffusionPipeline
            import torch
            
            # Stage 1: IF-I-XL-v1.0 (text to 64x64)
            logger.info("Loading IF-I-XL-v1.0...")
            _model_cache['stage_1'] = DiffusionPipeline.from_pretrained(
                "DeepFloyd/IF-I-XL-v1.0",
                variant="fp16",
                torch_dtype=torch.float16,
            )
            _model_cache['stage_1'].to('cuda')
            _model_cache['stage_1'].enable_model_cpu_offload()
            
            # Stage 2: IF-II-L-v1.0 (64x64 to 256x256)
            logger.info("Loading IF-II-L-v1.0...")
            _model_cache['stage_2'] = DiffusionPipeline.from_pretrained(
                "DeepFloyd/IF-II-L-v1.0",
                text_encoder=None,
                variant="fp16",
                torch_dtype=torch.float16,
            )
            _model_cache['stage_2'].to('cuda')
            _model_cache['stage_2'].enable_model_cpu_offload()
            
            # Stage 3: Stable Diffusion x4 Upscaler (256x256 to 1024x1024)
            logger.info("Loading stable-diffusion-x4-upscaler...")
            _model_cache['stage_3'] = DiffusionPipeline.from_pretrained(
                "stabilityai/stable-diffusion-x4-upscaler",
                torch_dtype=torch.float16,
            )
            _model_cache['stage_3'].to('cuda')
            _model_cache['stage_3'].enable_model_cpu_offload()
            
            _model_cache['loaded'] = True
            logger.info("DeepFloyd: All models loaded successfully")
            
        except Exception as e:
            logger.error(f"DeepFloyd: Failed to load models: {e}")
            _model_cache['loading'] = False
            raise
        finally:
            _model_cache['loading'] = False
    
    @classmethod
    def unload_models(cls):
        """Unload models from GPU memory to free VRAM."""
        global _model_cache
        
        for key in ['stage_1', 'stage_2', 'stage_3']:
            if _model_cache[key] is not None:
                del _model_cache[key]
                _model_cache[key] = None
        
        _model_cache['loaded'] = False
        
        # Force garbage collection
        import gc
        gc.collect()
        
        try:
            import torch
            torch.cuda.empty_cache()
        except:
            pass
        
        logger.info("DeepFloyd: Models unloaded from GPU")
    
    def generate(
        self,
        prompt: str,
        style: str = 'photorealistic',
        seed: int = None,
        num_inference_steps: int = 50,
        guidance_scale: float = 7.5,
    ) -> dict:
        """
        Generate an image from a text prompt.
        
        Args:
            prompt: Text description of the image to generate
            style: One of 'photorealistic', 'artistic', 'anime'
            seed: Random seed for reproducibility (optional)
            num_inference_steps: Number of denoising steps (default 50)
            guidance_scale: Classifier-free guidance scale (default 7.5)
            
        Returns:
            dict with keys:
                - 'image_path': Relative path to saved image
                - 'seed': The seed used for generation
                - 'steps': Number of inference steps used
                - 'nsfw_detected': Whether NSFW content was detected
        """
        if seed is None:
            seed = random.randint(0, 2147483647)
        
        # Apply style modifier to prompt
        style_modifier = self.STYLE_MODIFIERS.get(style, '')
        full_prompt = f"{style_modifier}{prompt}"
        
        logger.info(f"DeepFloyd: Generating image with prompt: '{prompt[:50]}...'")
        logger.info(f"DeepFloyd: Style: {style}, Seed: {seed}, Steps: {num_inference_steps}")
        
        if self.mock_mode:
            return self._generate_mock(prompt, style, seed, num_inference_steps)
        
        return self._generate_real(
            full_prompt,
            seed,
            num_inference_steps,
            guidance_scale
        )
    
    def _generate_mock(
        self,
        prompt: str,
        style: str,
        seed: int,
        steps: int
    ) -> dict:
        """Generate a placeholder image for testing without GPU."""
        import io
        from PIL import Image, ImageDraw, ImageFont
        
        # Simulate processing time
        time.sleep(2)
        
        # Create a placeholder image
        width, height = 1024, 1024
        
        # Create gradient background based on style
        img = Image.new('RGB', (width, height))
        
        if style == 'photorealistic':
            # Blue gradient
            for y in range(height):
                r = int(50 + (y / height) * 100)
                g = int(100 + (y / height) * 100)
                b = int(180 + (y / height) * 75)
                for x in range(width):
                    img.putpixel((x, y), (r, g, b))
        elif style == 'artistic':
            # Orange/red gradient
            for y in range(height):
                r = int(200 + (y / height) * 55)
                g = int(100 + (y / height) * 50)
                b = int(50 + (y / height) * 50)
                for x in range(width):
                    img.putpixel((x, y), (r, g, b))
        else:  # anime
            # Purple/pink gradient
            for y in range(height):
                r = int(180 + (y / height) * 75)
                g = int(100 + (y / height) * 50)
                b = int(200 + (y / height) * 55)
                for x in range(width):
                    img.putpixel((x, y), (r, g, b))
        
        # Add text overlay
        draw = ImageDraw.Draw(img)
        
        # Draw mock indicator
        text_lines = [
            "[MOCK MODE]",
            "DeepFloyd IF",
            "",
            f"Style: {style}",
            f"Seed: {seed}",
            "",
            "Prompt:",
            prompt[:60] + ('...' if len(prompt) > 60 else '')
        ]
        
        y_position = height // 3
        for line in text_lines:
            # Simple text centering (approximate)
            x_position = width // 2 - len(line) * 4
            draw.text((x_position, y_position), line, fill=(255, 255, 255))
            y_position += 30
        
        # Save to storage
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        filename = f"generated/mock_{seed}_{int(time.time())}.png"
        saved_path = default_storage.save(filename, ContentFile(buffer.read()))
        
        logger.info(f"DeepFloyd: Mock image saved to {saved_path}")
        
        return {
            'image_path': saved_path,
            'seed': seed,
            'steps': steps,
            'nsfw_detected': False,
        }
    
    def _generate_real(
        self,
        prompt: str,
        seed: int,
        num_inference_steps: int,
        guidance_scale: float
    ) -> dict:
        """Generate image using actual DeepFloyd IF models."""
        import torch
        import io
        
        global _model_cache
        
        if not _model_cache['loaded']:
            self.load_models()
        
        generator = torch.Generator(device='cuda').manual_seed(seed)
        nsfw_detected = False
        
        try:
            # Stage 1: Text to 64x64
            logger.info("DeepFloyd: Running Stage 1 (text to 64x64)...")
            prompt_embeds, negative_embeds = _model_cache['stage_1'].encode_prompt(prompt)
            
            stage_1_output = _model_cache['stage_1'](
                prompt_embeds=prompt_embeds,
                negative_prompt_embeds=negative_embeds,
                generator=generator,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                output_type="pt",
            )
            
            # Check NSFW
            if hasattr(stage_1_output, 'nsfw_content_detected') and stage_1_output.nsfw_content_detected:
                nsfw_detected = True
                logger.warning("DeepFloyd: NSFW content detected in Stage 1")
            
            # Stage 2: 64x64 to 256x256
            logger.info("DeepFloyd: Running Stage 2 (64x64 to 256x256)...")
            stage_2_output = _model_cache['stage_2'](
                image=stage_1_output.images,
                prompt_embeds=prompt_embeds,
                negative_prompt_embeds=negative_embeds,
                generator=generator,
                num_inference_steps=num_inference_steps // 2,
                guidance_scale=guidance_scale,
                output_type="pt",
            )
            
            # Stage 3: 256x256 to 1024x1024
            logger.info("DeepFloyd: Running Stage 3 (256x256 to 1024x1024)...")
            stage_3_output = _model_cache['stage_3'](
                prompt=prompt,
                image=stage_2_output.images,
                generator=generator,
                num_inference_steps=num_inference_steps // 4,
                guidance_scale=guidance_scale,
            )
            
            final_image = stage_3_output.images[0]
            
            # Save to storage
            buffer = io.BytesIO()
            final_image.save(buffer, format='PNG')
            buffer.seek(0)
            
            filename = f"generated/deepfloyd_{seed}_{int(time.time())}.png"
            saved_path = default_storage.save(filename, ContentFile(buffer.read()))
            
            logger.info(f"DeepFloyd: Image saved to {saved_path}")
            
            return {
                'image_path': saved_path,
                'seed': seed,
                'steps': num_inference_steps,
                'nsfw_detected': nsfw_detected,
            }
            
        except Exception as e:
            logger.error(f"DeepFloyd: Generation failed: {e}")
            raise
    
    def check_safety(self, image_path: str) -> bool:
        """
        Run additional NSFW safety check on generated image.
        
        Returns True if image is safe, False if NSFW detected.
        """
        if self.mock_mode:
            return True
        
        # TODO: Implement additional safety checker if needed
        # DeepFloyd has built-in safety checker
        return True


# Warm-up function for worker startup
def warmup_models():
    """Call this during Celery worker startup to pre-load models."""
    if not MOCK_MODE:
        try:
            DeepFloydService.load_models()
            logger.info("DeepFloyd: Models pre-loaded for worker")
        except Exception as e:
            logger.error(f"DeepFloyd: Warmup failed: {e}")
