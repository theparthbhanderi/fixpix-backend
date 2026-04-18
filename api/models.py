from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
import uuid

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} Profile"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if not hasattr(instance, 'profile'):
        UserProfile.objects.get_or_create(user=instance)

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
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        if self.source == 'generated':
            return f"Generated - {self.id}"
        return f"{self.processing_type} - {self.id}"

@receiver(post_save, sender=ImageProject)
def update_image_completed_at(sender, instance, **kwargs):
    """Update completed_at timestamp when status reaches terminal state."""
    from django.utils import timezone
    if instance.status in ['completed', 'failed'] and not instance.completed_at:
        # We use update() to avoid recursion on post_save
        ImageProject.objects.filter(id=instance.id).update(completed_at=timezone.now())


class FactCheckRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    original_claim = models.TextField(null=True, blank=True)
    claim = models.TextField()
    verdict = models.CharField(max_length=50)
    confidence = models.FloatField()
    explanation = models.TextField()
    evidence = models.JSONField(default=list, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.verdict} - {self.claim[:30]}..."


class SharedResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    query = models.TextField()
    data = models.JSONField()  # Full FactCheckResult snapshot
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Shared: {self.query[:30]} ({self.id})"


# --- FixPix OSINT v2: Forensic Archive Models ---

class IntelligenceReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    topic = models.TextField()
    verdict = models.CharField(max_length=50)
    confidence_score = models.FloatField(default=0)
    bias_score = models.FloatField(default=0)
    summary = models.TextField()
    reasoning = models.JSONField(default=list)  # Forensic Chain
    context = models.TextField(null=True, blank=True)
    sources = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    data_mode = models.CharField(max_length=20, default="real")

    def __str__(self):
        return f"Report: {self.topic[:30]} ({self.verdict})"

class EntityMention(models.Model):
    ENTITY_TYPES = [
        ('person', 'Person'),
        ('org', 'Organization'),
        ('loc', 'Location'),
        ('event', 'Event'),
    ]
    report = models.ForeignKey(IntelligenceReport, related_name='entities', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES)
    frequency = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.name} ({self.entity_type})"

class NarrativeMap(models.Model):
    report = models.ForeignKey(IntelligenceReport, related_name='narratives', on_delete=models.CASCADE)
    narrative_type = models.CharField(max_length=100) # e.g., "pro-India", "neutral"
    sentiment_score = models.FloatField() # -1 to 1
    volume_percentage = models.FloatField() # 0 to 100

    def __str__(self):
        return f"Narrative: {self.narrative_type} for {self.report.id}"

# --- Supabase Migration Models ---

class EditHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='edit_history')
    tool = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict, blank=True)
    output_url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.user.username} - {self.tool} at {self.created_at}"

class ChatHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_history')
    message = models.TextField()
    role = models.CharField(max_length=50) # 'user' or 'assistant'
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.user.username} - {self.role}: {self.message[:20]}"

class WorkflowHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workflow_history')
    title = models.CharField(max_length=200)
    steps = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.user.username} workflow: {self.title}"


class ContactMessage(models.Model):
    CATEGORY_CHOICES = [
        ('Technical Support', 'Technical Support'),
        ('Enterprise API', 'Enterprise API'),
        ('Billing & Subscriptions', 'Billing & Subscriptions'),
        ('Feature Request', 'Feature Request'),
        ('Other', 'Other'),
    ]
    STATUS_CHOICES = [
        ('unread', 'Unread'),
        ('read', 'Read'),
        ('replied', 'Replied'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Other')
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unread')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.name} ({self.category})"

