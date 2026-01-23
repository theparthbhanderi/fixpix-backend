from rest_framework import serializers
from django.contrib.auth.models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import ImageProject

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token['username'] = user.username
        token['email'] = user.email
        token['is_staff'] = user.is_staff
        token['is_superuser'] = user.is_superuser

        return token

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email')

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ('username', 'password', 'email')

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            password=validated_data['password'],
            email=validated_data.get('email', '')
        )
        return user

class SettingsSerializer(serializers.Serializer):
    removeScratches = serializers.BooleanField(required=False)
    faceRestoration = serializers.BooleanField(required=False)
    colorize = serializers.BooleanField(required=False)
    upscaleX = serializers.IntegerField(min_value=1, max_value=4, required=False)
    autoEnhance = serializers.BooleanField(required=False)
    whiteBalance = serializers.BooleanField(required=False)
    denoiseStrength = serializers.IntegerField(min_value=0, max_value=100, required=False)
    removeBackground = serializers.BooleanField(required=False)
    filterPreset = serializers.CharField(max_length=20, required=False)
    brightness = serializers.FloatField(min_value=0.0, max_value=3.0, required=False)
    contrast = serializers.FloatField(min_value=0.0, max_value=3.0, required=False)
    saturation = serializers.FloatField(min_value=0.0, max_value=3.0, required=False)


class ImageProjectSerializer(serializers.ModelSerializer):
    settings = SettingsSerializer(required=False)
    
    class Meta:
        model = ImageProject
        fields = (
            'id', 'user', 'original_image', 'processed_image', 
            'processing_type', 'settings', 'created_at', 'status',
            # AI Generation fields
            'source', 'prompt', 'gen_style', 'gen_seed', 'gen_steps'
        )
        read_only_fields = (
            'processed_image', 'user', 'created_at', 'status',
            'gen_seed', 'gen_steps'
        )

    def validate_settings(self, value):
        # Additional custom validation if needed
        return value

