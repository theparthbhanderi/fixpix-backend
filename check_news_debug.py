import os
import django
from django.conf import settings

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from api.services.news_service import get_fast_news

print(f"NEWS_API_KEY: {os.environ.get('NEWS_API_KEY')}")
print(f"GNEWS_API_KEY: {os.environ.get('GNEWS_API_KEY')}")

news = get_fast_news(limit=5)
print(f"Fetched news count: {len(news)}")
if news:
    print(f"Sample: {news[0]['title']}")
else:
    print("No news fetched.")
