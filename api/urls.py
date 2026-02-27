from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ImageViewSet, RegisterView, MyTokenObtainPairView, ProfileView
from .cloudflare_views import generate_image_proxy
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)

router = DefaultRouter()
router.register(r'images', ImageViewSet, basename='imageproject')

urlpatterns = [
    path('', include(router.urls)),
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('profile/', ProfileView.as_view(), name='user_profile'),
    # AI Text-to-Image (Cloudflare Worker proxy)
    path('generate/text-to-image/', generate_image_proxy, name='generate_image_proxy'),
    # Admin API
    path('admin/', include('api.admin_urls')),
]

