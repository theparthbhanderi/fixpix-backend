import os
from google.oauth2 import id_token
from google.auth.transport import requests
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken

# Environment variable for your Google Client ID
# We don't hardcode it so we can configure it per deployment
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip().replace('"', '').replace("'", "")

def verify_google_oauth2_token(token: str):
    """
    Verifies a Google OAuth2 token and returns the user info dictionary.
    Raises ValueError if token is invalid or client ID doesn't match.
    """
    try:
        # If GOOGLE_CLIENT_ID is not configured, we might optionally skip audience check in local testing
        # but in production it's critical.
        if not GOOGLE_CLIENT_ID:
            # Fallback for when ID isn't configured yet (allows initial testing if needed, though insecure)
            # In a real production setup, remove this fallback.
            idinfo = id_token.verify_oauth2_token(token, requests.Request())
        else:
            idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
            
        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            raise ValueError('Wrong issuer.')

        return idinfo
    except ValueError as e:
        raise ValueError(f"Invalid Google Token: {str(e)}")


def get_or_create_user_from_google(idinfo: dict) -> User:
    """
    Takes Google payload and returns a Django User object.
    Matches primarily by email.
    """
    email = idinfo.get('email')
    first_name = idinfo.get('given_name', '')
    last_name = idinfo.get('family_name', '')
    name = idinfo.get('name', '')
    
    # Try looking up by email
    user = User.objects.filter(email=email).first()
    
    if user:
        return user
        
    # User doesn't exist, create one
    # Use part of the email and a random string for a unique username
    base_username = email.split('@')[0] if email else 'user'
    
    import random
    import string
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    username = f"{base_username}_{random_suffix}"
    
    # Ensure uniqueness
    while User.objects.filter(username=username).exists():
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        username = f"{base_username}_{random_suffix}"
        
    user = User.objects.create_user(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name
    )
    # Give them a random unuseable password so they only login via Google, 
    # unless they do a password reset later
    user.set_unusable_password()
    user.save()
    
    return user


def get_tokens_for_user(user: User):
    """
    Generate SimpleJWT token pair for a specific user.
    """
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }
