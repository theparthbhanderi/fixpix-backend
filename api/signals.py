from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import ImageProject
from django.core.files.storage import default_storage

@receiver(post_delete, sender=ImageProject)
def delete_project_files(sender, instance, **kwargs):
    """
    Delete associated files from storage when an ImageProject is deleted.
    Prevents storage bloat from orphan record files.
    """
    if instance.original_image:
        try:
            # Check if file exists to avoid errors on shared files (unlikely here)
            if default_storage.exists(instance.original_image.name):
                default_storage.delete(instance.original_image.name)
        except Exception:
            pass # Fail silently on cleanup

    if instance.processed_image:
        try:
             if default_storage.exists(instance.processed_image.name):
                default_storage.delete(instance.processed_image.name)
        except Exception:
            pass
