from django.db import models
from django.contrib.auth.models import User
import uuid

class ImageProject(models.Model):
    PROCESSING_TYPES = [
        ('colorize', 'Colorize'),
        ('restore', 'Restore'),
        ('upscale', 'Upscale'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    SOURCE_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('generated', 'Generated'),
    ]
    GEN_STYLE_CHOICES = [
        ('photorealistic', 'Photorealistic'),
        ('artistic', 'Artistic'),
        ('anime', 'Anime'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    original_image = models.ImageField(upload_to='originals/', null=True, blank=True)
    processed_image = models.ImageField(upload_to='processed/', null=True, blank=True)
    processing_type = models.CharField(max_length=20, choices=PROCESSING_TYPES, default='restore')
    settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # AI Generation fields
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='uploaded')
    prompt = models.TextField(null=True, blank=True, help_text='Text prompt for AI generation')
    gen_style = models.CharField(max_length=20, choices=GEN_STYLE_CHOICES, null=True, blank=True)
    gen_steps = models.IntegerField(null=True, blank=True, help_text='Number of inference steps')
    gen_seed = models.IntegerField(null=True, blank=True, help_text='Random seed for reproducibility')

    def __str__(self):
        if self.source == 'generated':
            return f"Generated - {self.id}"
        return f"{self.processing_type} - {self.id}"

