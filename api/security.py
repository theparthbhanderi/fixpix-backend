"""
Security Utilities for FixPix

File validation, sanitization, and security checks for uploaded images.
"""

import os
import magic
import hashlib
from PIL import Image
from django.core.exceptions import ValidationError


# Allowed MIME types for images
ALLOWED_MIME_TYPES = {
    'image/jpeg': ['.jpg', '.jpeg'],
    'image/png': ['.png'],
    'image/webp': ['.webp'],
    'image/gif': ['.gif'],
    'image/bmp': ['.bmp'],
    'image/tiff': ['.tiff', '.tif'],
}

# Maximum file size (20MB)
MAX_FILE_SIZE = 20 * 1024 * 1024

# Maximum image dimensions
MAX_DIMENSION = 10000  # 10k pixels


def validate_uploaded_file(file):
    """
    Comprehensive file validation for uploaded images.
    
    Checks:
    1. File size
    2. File extension
    3. MIME type (magic bytes)
    4. Image validity (can be opened by PIL)
    5. Image dimensions
    
    Returns: (is_valid, error_message)
    """
    errors = []
    
    # 1. Check file size
    if file.size > MAX_FILE_SIZE:
        errors.append(f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # 2. Check file extension
    ext = os.path.splitext(file.name)[1].lower()
    valid_extensions = [e for exts in ALLOWED_MIME_TYPES.values() for e in exts]
    if ext not in valid_extensions:
        errors.append(f"Invalid file extension. Allowed: {', '.join(valid_extensions)}")
    
    # 3. Check MIME type using magic bytes
    try:
        file.seek(0)
        file_header = file.read(2048)
        file.seek(0)
        
        mime = magic.from_buffer(file_header, mime=True)
        if mime not in ALLOWED_MIME_TYPES:
            errors.append(f"Invalid file type: {mime}. Only images are allowed.")
    except Exception as e:
        errors.append(f"Could not verify file type: {str(e)}")
    
    # 4. Validate image can be opened by PIL
    try:
        file.seek(0)
        img = Image.open(file)
        img.verify()  # Verify it's actually an image
        file.seek(0)
        
        # Reopen for dimension check (verify closes the file)
        img = Image.open(file)
        width, height = img.size
        file.seek(0)
        
        # 5. Check dimensions
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            errors.append(f"Image dimensions too large. Max: {MAX_DIMENSION}x{MAX_DIMENSION}")
            
    except Exception as e:
        errors.append(f"Invalid or corrupted image file: {str(e)}")
    
    if errors:
        return False, "; ".join(errors)
    
    return True, None


def sanitize_filename(filename):
    """
    Sanitize filename to prevent path traversal and other attacks.
    """
    # Remove path components
    filename = os.path.basename(filename)
    
    # Remove null bytes and other dangerous characters
    dangerous_chars = ['..', '/', '\\', '\x00', '\n', '\r', '<', '>', ':', '"', '|', '?', '*']
    for char in dangerous_chars:
        filename = filename.replace(char, '')
    
    # Limit length
    name, ext = os.path.splitext(filename)
    if len(name) > 100:
        name = name[:100]
    
    # Ensure extension is safe
    ext = ext.lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']:
        ext = '.jpg'
    
    # Generate unique filename with hash
    unique_suffix = hashlib.md5(str(os.urandom(8)).encode()).hexdigest()[:8]
    
    return f"{name}_{unique_suffix}{ext}"


def check_image_for_malware(file):
    """
    Basic check for embedded malware in images.
    
    Checks for:
    1. PHP tags embedded in image
    2. JavaScript in image metadata
    3. Executable headers
    """
    file.seek(0)
    content = file.read()
    file.seek(0)
    
    # Check for suspicious patterns
    suspicious_patterns = [
        b'<?php',
        b'<?=',
        b'<script',
        b'javascript:',
        b'MZ',  # Windows executable
        b'\x7fELF',  # Linux executable
    ]
    
    for pattern in suspicious_patterns:
        if pattern in content[:10000]:  # Check first 10KB
            return False, f"Suspicious content detected in file"
    
    return True, None


class SecureFileValidator:
    """
    Django-compatible file validator class.
    
    Usage in models:
        image = models.ImageField(validators=[SecureFileValidator()])
    """
    
    def __init__(self, max_size=MAX_FILE_SIZE, allowed_types=None):
        self.max_size = max_size
        self.allowed_types = allowed_types or ALLOWED_MIME_TYPES
    
    def __call__(self, file):
        is_valid, error = validate_uploaded_file(file)
        if not is_valid:
            raise ValidationError(error)
        
        is_safe, malware_error = check_image_for_malware(file)
        if not is_safe:
            raise ValidationError(malware_error)
