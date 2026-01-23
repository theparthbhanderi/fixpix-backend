"""
AI Filter Presets for FixPix
Professional-grade photo filter configurations
"""

import cv2
import numpy as np

# Filter Preset Configurations
PRESETS = {
    'vintage': {
        'sepia_strength': 0.7,
        'contrast': 1.15,
        'saturation': 0.8,
        'vignette': True,
        'warmth': 20
    },
    'cinematic': {
        'contrast': 1.3,
        'saturation': 0.9,
        'shadows_blue': 30,
        'highlights_orange': 20,
        'vignette': True
    },
    'warm': {
        'temperature': 40,
        'saturation': 1.15,
        'contrast': 1.05
    },
    'cool': {
        'temperature': -40,
        'saturation': 1.05,
        'contrast': 1.05
    },
    'bw_classic': {
        'grayscale': True,
        'contrast': 1.2,
        'clarity': 1.1
    },
    'bw_noir': {
        'grayscale': True,
        'contrast': 1.4,
        'vignette': True,
        'clarity': 1.2
    },
    'fade': {
        'fade_strength': 0.25,
        'saturation': 0.85,
        'contrast': 0.9
    },
    'vivid': {
        'saturation': 1.4,
        'contrast': 1.15,
        'clarity': 1.1
    }
}


def apply_sepia(img, strength=0.5):
    """Apply sepia tone effect"""
    # Correct matrix for BGR (swapped R and B coefficients relative to RGB matrix)
    sepia_kernel = np.array([
        [0.131, 0.534, 0.272],  # Blue channel formula
        [0.168, 0.686, 0.349],  # Green channel formula
        [0.189, 0.769, 0.393]   # Red channel formula
    ])
    sepia = cv2.transform(img, sepia_kernel)
    sepia = np.clip(sepia, 0, 255).astype(np.uint8)
    # Blend with original based on strength
    return cv2.addWeighted(img, 1 - strength, sepia, strength, 0)


def apply_vignette(img, strength=0.5):
    """Apply vignette (dark corners) effect"""
    rows, cols = img.shape[:2]
    
    # Create gradient mask
    X = cv2.getGaussianKernel(cols, cols * 0.5)
    Y = cv2.getGaussianKernel(rows, rows * 0.5)
    kernel = Y * X.T
    mask = kernel / kernel.max()
    
    # Adjust strength
    mask = mask ** (1 - strength * 0.5)
    
    # Apply to each channel
    result = img.copy().astype(np.float32)
    for i in range(3):
        result[:, :, i] = result[:, :, i] * mask
    
    return np.clip(result, 0, 255).astype(np.uint8)


def adjust_temperature(img, temperature):
    """
    Adjust color temperature
    Positive = warmer (more yellow/orange)
    Negative = cooler (more blue)
    """
    result = img.copy().astype(np.float32)
    
    if temperature > 0:
        # Warm: increase red, decrease blue
        result[:, :, 2] = np.clip(result[:, :, 2] + temperature, 0, 255)  # Red
        result[:, :, 0] = np.clip(result[:, :, 0] - temperature * 0.5, 0, 255)  # Blue
    else:
        # Cool: increase blue, decrease red
        result[:, :, 0] = np.clip(result[:, :, 0] - temperature, 0, 255)  # Blue
        result[:, :, 2] = np.clip(result[:, :, 2] + temperature * 0.5, 0, 255)  # Red
    
    return result.astype(np.uint8)


def apply_fade(img, strength=0.15):
    """Apply faded/matte look by lifting blacks"""
    result = img.copy().astype(np.float32)
    lift = strength * 255
    result = result + lift
    result = np.clip(result, 0, 255)
    return result.astype(np.uint8)


def apply_clarity(img, strength=1.1):
    """Enhance midtone contrast (clarity)"""
    # High-pass filter approach
    blur = cv2.GaussianBlur(img, (0, 0), 50)
    highpass = cv2.addWeighted(img, 2, blur, -1, 0)
    
    # Blend based on strength
    blend_factor = (strength - 1) * 0.5
    result = cv2.addWeighted(img, 1, highpass, blend_factor, 0)
    return np.clip(result, 0, 255).astype(np.uint8)


def apply_shadows_tint(img, blue_shift=0, orange_shift=0):
    """Tint shadows with color (cinematic look)"""
    result = img.copy().astype(np.float32)
    
    # Create luminance mask for shadows
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    shadow_mask = 1 - (gray / 255)  # Inverted: dark areas = 1
    shadow_mask = shadow_mask ** 2  # Concentrate on darker areas
    
    if blue_shift > 0:
        result[:, :, 0] = result[:, :, 0] + shadow_mask * blue_shift
    
    if orange_shift > 0:
        # Orange = red + some green
        highlight_mask = 1 - shadow_mask
        result[:, :, 2] = result[:, :, 2] + highlight_mask * orange_shift
        result[:, :, 1] = result[:, :, 1] + highlight_mask * (orange_shift * 0.3)
    
    return np.clip(result, 0, 255).astype(np.uint8)


def apply_preset(img, preset_name):
    """
    Apply a complete filter preset to an image
    Returns processed image
    """
    if preset_name not in PRESETS:
        return img
    
    config = PRESETS[preset_name]
    result = img.copy()
    
    # Apply grayscale first if needed
    if config.get('grayscale', False):
        gray = cv2.cvtColor(result, cv2.COLOR_BGR2GRAY)
        result = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    
    # Apply sepia
    if 'sepia_strength' in config:
        result = apply_sepia(result, config['sepia_strength'])
    
    # Apply temperature
    if 'temperature' in config:
        result = adjust_temperature(result, config['temperature'])
    
    # Apply warmth (similar to positive temperature)
    if 'warmth' in config:
        result = adjust_temperature(result, config['warmth'])
    
    # Apply shadows/highlights tint
    if 'shadows_blue' in config or 'highlights_orange' in config:
        result = apply_shadows_tint(
            result,
            blue_shift=config.get('shadows_blue', 0),
            orange_shift=config.get('highlights_orange', 0)
        )
    
    # Apply fade
    if 'fade_strength' in config:
        result = apply_fade(result, config['fade_strength'])
    
    # Apply contrast
    if 'contrast' in config and config['contrast'] != 1.0:
        alpha = config['contrast']
        result = cv2.convertScaleAbs(result, alpha=alpha, beta=0)
    
    # Apply saturation
    if 'saturation' in config and config['saturation'] != 1.0:
        hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = hsv[:, :, 1] * config['saturation']
        hsv[:, :, 1] = np.clip(hsv[:, :, 1], 0, 255)
        result = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    
    # Apply clarity
    if 'clarity' in config:
        result = apply_clarity(result, config['clarity'])
    
    # Apply vignette last
    if config.get('vignette', False):
        result = apply_vignette(result, 0.6)
    
    return result


def get_available_presets():
    """Return list of available preset names"""
    return list(PRESETS.keys())
