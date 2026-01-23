from django.contrib import admin
from django.utils.html import format_html
from .models import ImageProject

@admin.register(ImageProject)
class ImageProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_info', 'processing_type', 'status_badge', 'created_at', 'preview_image')
    list_filter = ('status', 'processing_type', 'created_at', 'source')
    search_fields = ('user__username', 'user__email', 'id', 'prompt')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)

    def user_info(self, obj):
        return obj.user.username if obj.user else "Anonymous"
    user_info.short_description = 'User'

    def status_badge(self, obj):
        colors = {
            'completed': 'green',
            'processing': 'blue',
            'pending': 'orange',
            'failed': 'red'
        }
        color = colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: white; background-color: {}; padding: 3px 10px; border-radius: 10px; font-weight: bold;">{}</span>',
            color,
            obj.status.upper()
        )
    status_badge.short_description = 'Status'

    def preview_image(self, obj):
        if obj.processed_image:
            return format_html('<img src="{}" style="width: 50px; height: 50px; object-fit: cover; border-radius: 5px;" />', obj.processed_image.url)
        elif obj.original_image:
             return format_html('<img src="{}" style="width: 50px; height: 50px; object-fit: cover; border-radius: 5px;" />', obj.original_image.url)
        return "-"
    preview_image.short_description = 'Preview'
