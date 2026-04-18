import requests
import os
import logging
import time
from collections import Counter
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Stopwords to filter out from trends
STOPWORDS = {
    'the', 'is', 'at', 'which', 'on', 'and', 'a', 'in', 'to', 'for', 'with', 'that', 'by', 'as', 
    'be', 'of', 'it', 'from', 'this', 'are', 'was', 'were', 'been', 'has', 'have', 'had', 'do', 
    'does', 'did', 'but', 'if', 'or', 'about', 'more', 'all', 'one', 'no', 'up', 'down', 'out',
    'over', 'under', 'again', 'then', 'once', 'india', 'news', 'breaking', 'world', 'latest',
    'u.s.', 'says', 'amid', 'war', 'live', 'updates', 'watch', 'video', 'st', 'th', 'rd', 'nd'
}

def get_live_trends():
    """Fetch and calculate trending topics from live news."""
    cache_key = "current_trending_topics"
    cached = cache.get(cache_key)
    if cached:
        return cached

    trends = []
    try:
        # 1. Fetch live news (Using GNews as it's reliable for trends)
        api_key = os.getenv("GNEWS_API_KEY")
        if not api_key:
            return []

        url = f"https://gnews.io/api/v4/top-headlines?category=general&lang=en&apikey={api_key}"
        response = requests.get(url, timeout=5)
        data = response.json()

        if "articles" not in data:
            return []

        # 2. Extract Keywords from Titles
        all_titles = [article["title"] for article in data["articles"]]
        words = []
        for title in all_titles:
            # Clean and split
            cleaned = re.sub(r'[^\w\s]', '', title.lower())
            words += [w for w in cleaned.split() if w not in STOPWORDS and len(w) > 3]

        # 3. Count and Score
        counts = Counter(words).most_common(12)
        
        # 4. Create Trend Objects
        for word, count in counts:
            # Simple scoring: count + rarity bias
            score = min(0.99, (count / 10.0) + 0.3)
            
            trend_type = "global"
            if any(k in word for k in ["israel", "gaza", "russia", "ukraine", "conflict"]):
                trend_type = "conflict"
            elif any(k in word for k in ["ai", "tech", "apple", "google", "nvidia"]):
                trend_type = "tech"
            elif any(k in word for k in ["market", "economy", "fed", "inflation"]):
                trend_type = "economy"

            trends.append({
                "topic": word.capitalize(),
                "count": count,
                "type": trend_type,
                "trend_score": round(score, 2),
                "heat": "HOT" if score > 0.7 else "Rising" if score > 0.4 else "Stable"
            })

        # Cache for 5 minutes
        cache.set(cache_key, trends, 300)
        return trends

    except Exception as e:
        logger.error(f"Trend extraction failed: {str(e)}")
        return []

import re # Required for regex cleaning
