import requests
import os
import logging
import concurrent.futures
from urllib.parse import quote

logger = logging.getLogger(__name__)

def search_tavily(query, limit=5):
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []
    try:
        res = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": limit
            },
            timeout=10
        )
        data = res.json()
        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", "Tavily Result"),
                "source": r.get("url", "").split("/")[2] if "//" in r.get("url", "") else "Tavily",
                "url": r.get("url", ""),
                "text": r.get("content", ""),
                "type": "search"
            })
        return results
    except Exception as e:
        logger.error(f"Tavily Search Failed: {e}")
        return []

def search_google_fact_check(query, limit=3):
    api_key = os.environ.get("GOOGLE_FACT_CHECK_API_KEY")
    if not api_key:
        return []
    try:
        url = f"https://factchecktools.googleapis.com/v1alpha1/claims:search?query={quote(query)}&key={api_key}"
        res = requests.get(url, timeout=10)
        data = res.json()
        evidence = []
        claims = data.get("claims", [])
        if not isinstance(claims, list): claims = []
        for claim in claims[:limit]:
            review = claim.get("claimReview", [{}])[0]
            evidence.append({
                "title": f"Fact Check: {claim.get('text')}",
                "source": review.get("publisher", {}).get("name", "Google Fact Check"),
                "url": review.get("url", ""),
                "text": f"Verdict: {review.get('textualRating')}. Summary: {claim.get('text')}",
                "type": "fact_check"
            })
        return evidence
    except Exception as e:
        logger.error(f"Google Fact Check Failed: {e}")
        return []

def search_newsdata(query, limit=5):
    api_key = os.environ.get("NEWSDATA_API_KEY")
    if not api_key:
        return []
    try:
        url = f"https://newsdata.io/api/1/news?apikey={api_key}&q={quote(query)}&language=en"
        res = requests.get(url, timeout=10)
        data = res.json()
        results = []
        news_results = data.get("results", [])
        if not isinstance(news_results, list): news_results = []
        for a in news_results[:limit]:
            if a.get("description"):
                results.append({
                    "title": a.get("title", "News Article"),
                    "source": a.get("source_id", "NewsData"),
                    "url": a.get("link", ""),
                    "text": a.get("description", ""),
                    "type": "news"
                })
        return results
    except Exception as e:
        logger.error(f"NewsData Search Failed: {e}")
        return []

def search_gnews(query, limit=5):
    api_key = os.environ.get("GNEWS_API_KEY")
    if not api_key:
        return []
    try:
        url = f"https://gnews.io/api/v4/search?q={quote(query)}&token={api_key}&lang=en&max={limit}"
        res = requests.get(url, timeout=10)
        data = res.json()
        results = []
        for a in data.get("articles", []):
            results.append({
                "title": a.get("title", "GNews Article"),
                "source": a.get("source", {}).get("name", "GNews"),
                "url": a.get("url", ""),
                "text": a.get("description", ""),
                "type": "news"
            })
        return results
    except Exception as e:
        logger.error(f"GNews Search Failed: {e}")
        return []

def multi_source_search(query):
    logger.info(f"🌐 Advanced multi-source search for: {query}")
    
    evidence = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(search_tavily, query): "Tavily",
            executor.submit(search_google_fact_check, query): "GoogleFactCheck",
            executor.submit(search_newsdata, query): "NewsData",
            executor.submit(search_gnews, query): "GNews"
        }
        
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            try:
                results = future.result()
                if results:
                    evidence.extend(results)
                    logger.info(f"✅ {source} returned {len(results)} items.")
            except Exception as e:
                logger.error(f"❌ {source} thread failed: {e}")

    return evidence # Return all, factcheck_service will score and slice
