from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ImageViewSet, RegisterView, MyTokenObtainPairView, ProfileView, EditHistoryViewSet, ChatHistoryViewSet, WorkflowHistoryViewSet, ContactSubmissionView
from .cloudflare_views import generate_image_proxy, edit_image_proxy
from .chatbot_views import detect_intent, optimize_prompt_view, generate_tagline_view
from .copilot_views import execute_pipeline_view, ai_chat_stream_view
from .views_news import news_view
from . import views_news
from . import views_factcheck
from . import views
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)

from .views import GoogleLoginView
from .v1_ai_proxy import super_resolution_v1, inpaint_v1, colorize_v1
from .stability_views import (
    remove_background_view, 
    style_transfer_view, 
    conservative_upscale_view, 
    change_background_view,
    generate_sticker_view
)

router = DefaultRouter()
router.register(r'images', ImageViewSet, basename='image')
router.register(r'history/edits', EditHistoryViewSet, basename='edithistory')
router.register(r'history/chat', ChatHistoryViewSet, basename='chathistory')
router.register(r'history/workflows', WorkflowHistoryViewSet, basename='workflowhistory')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/google/', GoogleLoginView.as_view(), name='google_login'),
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('profile/', ProfileView.as_view(), name='user_profile'),
    path('generate/text-to-image/', generate_image_proxy, name='generate_image_proxy'),
    path('generate/edit-image/', edit_image_proxy, name='edit_image_proxy'),
    path('generate-image/', views.text_to_image, name='text_to_image_new'),
    
    # AI Chatbot System
    path('chatbot/detect-intent/', detect_intent, name='chatbot_detect_intent'),
    path('chatbot/optimize-prompt/', optimize_prompt_view, name='optimize_prompt'),
    path('chatbot/generate-tagline/', generate_tagline_view, name='generate_tagline'),
    path('ai/chat/stream/', ai_chat_stream_view, name='ai_chat_stream'),

    # Copilot Intelligence & Action Routing
    path('copilot/execute-pipeline/', execute_pipeline_view, name='copilot_execute_pipeline'),
    
    # Legacy AI v1 Proxy Endpoints
    path('v1/image/super-resolution/', super_resolution_v1, name='sr_v1_proxy'),
    path('v1/image/inpaint/', inpaint_v1, name='inpaint_v1_proxy'),
    path('v1/image/colorize/', colorize_v1, name='colorize_v1_proxy'),
    
    # Stability AI Direct Proxy Endpoints
    path('v1/image/remove-bg/', remove_background_view, name='remove_bg_proxy'),
    path('v1/image/change-bg/', change_background_view, name='change_bg_proxy'),
    path('v1/image/style-transfer/', style_transfer_view, name='style_transfer_proxy'),
    path('v1/image/upscale-conservative/', conservative_upscale_view, name='upscale_conservative_proxy'),
    
    # Sticker Engine (MIGRATED)
    path('v1/image/generate-sticker/', generate_sticker_view, name='generate_sticker_proxy'),
    path('v1/sticker/text-to-sticker/', generate_sticker_view, name='sticker_text_to_sticker'),
    path('v1/sticker/generate/', generate_sticker_view, name='sticker_generate'), # Reuse same view, it handles both
    
    # AI News & Fact Check System (Autobuild v2 - Optimized)
    path('news/feed/', views_news.live_news_feed, name='live_news_feed'),
    path('news/', news_view, name='news_view'),
    path('news/fast/', views_news.fast_news_view, name='fast_news_view'),
    path('news/fact-check-batch/', views_news.batch_fact_check_view, name='batch_fact_check_view'),
    path('fact-check/', views_factcheck.fact_check_view, name='fact_check_text'), 
    path('fact-check/screenshot/', views_factcheck.fact_check_screenshot, name='fact_check_screenshot'),
    path('news/trending/', views_news.get_trending_topics, name='trending_topics'),
    path('fact-check/link/', views_factcheck.fact_check_link, name='fact_check_link'),
    path('fact-check/image/', views_factcheck.fact_check_image, name='fact_check_image'),
    path('fact-check/suggestions/', views_factcheck.suggestions_view, name='search_suggestions'),
    path('fact-check/history/', views_factcheck.get_history, name='get_history'),
    path('fact-check/share/create/', views_factcheck.create_share_item, name='create_share_item'),
    path('fact-check/share/<uuid:share_id>/', views_factcheck.get_share_item, name='get_share_item'),
    
    path('admin/', include('api.admin_urls')),
    path('contact/', ContactSubmissionView.as_view(), name='contact_submit'),
]
