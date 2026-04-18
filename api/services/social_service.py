import requests
import logging
import re
from urllib.parse import quote

logger = logging.getLogger(__name__)

def fetch_duckduckgo_instant(query):
    """
    Fetch instant answers/abstracts from DuckDuckGo.
    """
    try:
        url = f"https://api.duckduckgo.com/?q={quote(query)}&format=json&no_html=1&skip_disambig=1"
        res = requests.get(url, timeout=5)
        data = res.json()
        
        results = []
        abstract = data.get("AbstractText")
        if abstract:
            results.append({
                "title": f"DuckDuckGo: {data.get('Heading', 'Summary')}",
                "source": "DuckDuckGo",
                "url": data.get("AbstractURL", ""),
                "text": abstract,
                "type": "instant_answer",
                "credibility": 90
            })
            
        # Related topics (handy for broader context)
        for topic in data.get("RelatedTopics", [])[:2]:
            if "Text" in topic:
                results.append({
                    "title": "Related Context",
                    "source": "DuckDuckGo",
                    "url": topic.get("FirstURL", ""),
                    "text": topic.get("Text", ""),
                    "type": "context_signal",
                    "credibility": 80
                })
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo API failed: {e}")
        return []

def fetch_reddit_sentiment(query):
    """
    Fetch public debate/sentiment from Reddit via public RSS.
    Note: Highly useful for 'rumors' or 'public pulse'.
    """
    try:
        # Search reddit via RSS for the query
        url = f"https://www.reddit.com/search.rss?q={quote(query)}&sort=relevance&t=week"
        # Reddit requires a User-Agent to avoid 429
        headers = {'User-Agent': 'Mozilla/5.0 (AI Hub Research Bot)'}
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code != 200:
            return []

        # Simple regex-based RSS parsing to avoid extra dependencies
        titles = re.findall(r'<title>(.*?)</title>', res.text)
        links = re.findall(r'<link href="(.*?)"', res.text)
        
        results = []
        # Skip first title (it's the feed title)
        for i in range(1, min(len(titles), 5)):
            title = titles[i]
            # Decode HTML entities
            title = title.replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
            
            results.append({
                "title": f"Public Debate: {title}",
                "source": "Reddit Public Pulse",
                "url": links[i] if i < len(links) else "https://reddit.com",
                "text": f"Recent public discussion found on Reddit regarding: {title}",
                "type": "social_sentiment",
                "credibility": 40 # Social sentiment is lower credibility but high intelligence value
            })
        return results
    except Exception as e:
        logger.error(f"Reddit Sentiment fetch failed: {e}")
        return []

def get_extra_intelligence(query):
    """
    Orchestrate multiple free intelligence signals.
    """
    signals = []
    
    # 1. DuckDuckGo Instant
    ddg = fetch_duckduckgo_instant(query)
    if ddg: signals.extend(ddg)
    
    # 2. Reddit Pulse (only for non-article specific queries to avoid noise)
    # If the query is short, it's likely a news topic
    if len(query.split()) < 10:
        reddit = fetch_reddit_sentiment(query)
        if reddit: signals.extend(reddit)
        
    return signals
