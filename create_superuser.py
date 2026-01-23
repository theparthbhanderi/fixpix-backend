import os
import django
from django.contrib.auth import get_user_model

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
django.setup()

User = get_user_model()

username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

if username and password:
    if not User.objects.filter(username=username).exists():
        print(f"Creating superuser {username}...")
        User.objects.create_superuser(username=username, email=email, password=password)
        print("Superuser created successfully.")
    else:
        print(f"Superuser {username} already exists.")
else:
    print("No DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD found. Skipping superuser creation.")
