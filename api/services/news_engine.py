import requests
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- API ROTATION HELPERS ---
def get_rotated_api_keys(env_var_name):
    """Parse comma-separated keys from environment."""
    keys_str = os.getenv(env_var_name, "")
    if not keys_str:
        return []
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def fetch_gnews(query):
    """Fetch news from GNews API with rotation."""
    keys = get_rotated_api_keys("GNEWS_API_KEY")
    if not keys:
        return []
        
    for key in keys:
        try:
            url = f"https://gnews.io/api/v4/search?q={query}&lang=en&max=5&token={key}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                articles = res.json().get("articles", [])
                return [{"title": a.get("title"), "source": a.get("source", {}).get("name", "GNews"), "url": a.get("url")} for a in articles]
            elif res.status_code == 429 or "limit reached" in res.text.lower():
                logger.warning(f"🚨 GNews Rate Limit hit for key {key[:8]}... Rotating.")
                continue
        except Exception as e:
            logger.error(f"Error fetching GNews: {e}")
            continue
    return []

def fetch_newsdata(query):
    """Fetch news from NewsData.io API with rotation."""
    keys = get_rotated_api_keys("NEWSDATA_API_KEY")
    if not keys:
        return []

    for key in keys:
        try:
            url = f"https://newsdata.io/api/1/news?apikey={key}&q={query}&language=en"
            res = requests.get(url, timeout=5)
            data = res.json()
            if res.status_code == 200 and data.get("status") != "error":
                results = data.get("results", [])
                return [{"title": r.get("title"), "source": r.get("source_id", "NewsData"), "url": r.get("link")} for r in results]
            elif "exceeded" in str(data).lower() or data.get("code") == "ApiLimitExceeded":
                logger.warning(f"🚨 NewsData Limit hit for key {key[:8]}... Rotating.")
                continue
        except Exception as e:
            logger.error(f"Error fetching NewsData: {e}")
            continue
    return []

def fetch_tavily(query):
    """Fetch high-fidelity OSINT signals from Tavily with rotation."""
    keys = get_rotated_api_keys("TAVILY_API_KEY")
    if not keys:
        return []

    for key in keys:
        try:
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "max_results": 5
            }
            res = requests.post(url, json=payload, timeout=7)
            data = res.json()
            if res.status_code == 200:
                results = data.get("results", [])
                return [{"title": r.get("title"), "source": "Tavily Intelligence", "url": r.get("url")} for r in results]
            elif res.status_code == 429 or "exceed" in str(data).lower():
                logger.warning(f"🚨 Tavily Limit hit for key {key[:8]}... Rotating.")
                continue
        except Exception as e:
            logger.error(f"Error fetching Tavily: {e}")
            continue
    return []

def fetch_serper(query):
    """Fetch high-fidelity Google Search data from Serper.dev."""
    keys = get_rotated_api_keys("SERPER_API_KEY")
    if not keys:
        return {"results": [], "paa": [], "related": []}

    for key in keys:
        try:
            url = "https://google.serper.dev/search"
            payload = {"q": query, "num": 10}
            headers = {"X-API-KEY": key, "Content-Type": "application/json"}
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                results = [{"title": r.get("title"), "url": r.get("link"), "source": "Google/Serper"} for r in data.get("organic", [])]
                paa = [q.get("question") for q in data.get("peopleAlsoAsk", [])]
                related = [q.get("query") for q in data.get("relatedSearches", [])]
                return {"results": results, "paa": paa, "related": related}
        except Exception as e:
            logger.error(f"Error fetching Serper: {e}")
            continue
    return {"results": [], "paa": [], "related": []}

def fetch_searchapi(query):
    """Fetch high-fidelity Google Search data from SearchApi.io."""
    keys = get_rotated_api_keys("SEARCHAPI_API_KEY")
    if not keys:
        return {"results": [], "paa": [], "related": []}

    for key in keys:
        try:
            url = f"https://www.searchapi.io/api/v1/search?engine=google&q={query}&api_key={key}"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                results = [{"title": r.get("title"), "url": r.get("link"), "source": "Google/SearchApi"} for r in data.get("organic_results", [])]
                paa = [q.get("question") for q in data.get("people_also_ask", [])]
                related = [q.get("query") for q in data.get("related_searches", [])]
                return {"results": results, "paa": paa, "related": related}
        except Exception as e:
            logger.error(f"Error fetching SearchApi: {e}")
            continue
    return {"results": [], "paa": [], "related": []}

def fetch_google_news_rss(query):
    """Fetch high-fidelity news headlines from Google News RSS (Zero-Key)."""
    try:
        # Encode query for URL
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code != 200:
            return []
            
        soup = BeautifulSoup(res.content, features="xml")
        items = soup.find_all('item')[:10]
        
        results = []
        for item in items:
            title = item.title.text if item.title else ""
            link = item.link.text if item.link else ""
            source = item.source.text if item.source else "Google News"
            
            if title and link:
                results.append({
                    "title": title,
                    "source": source,
                    "url": link
                })
        return results
    except Exception as e:
        logger.error(f"Error fetching Google News RSS: {e}")
        return []

def fetch_duckduckgo(query):
    """Fetch news/info from DuckDuckGo."""
    try:
        # Improved: Use the RSS/search results if possible, but DDG API is limited
        url = f"https://api.duckduckgo.com/?q={query}&format=json"
        res = requests.get(url, timeout=5)
# ... continues
        if res.status_code == 200:
            data = res.json()
            results = []
            if data.get("AbstractText"):
                results.append({"title": data.get("AbstractText"), "source": "DuckDuckGo", "url": data.get("AbstractURL", "")})
            for topic in data.get("RelatedTopics", [])[:3]:
                if "Text" in topic:
                    results.append({"title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "), "source": "DuckDuckGo", "url": topic.get("FirstURL", "")})
            return results
        return []
    except Exception as e:
        logger.error(f"Error fetching DuckDuckGo: {e}")
        return []

def fetch_all_intelligence(query):
    """v2 Intelligence Context Gatherer (Parallel Execution)."""
    logger.info(f"🔍 Orchestrating Multi-Source Intelligence for: {query}")
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        f_serper = executor.submit(fetch_serper, query)
        f_searchapi = executor.submit(fetch_searchapi, query)
        f_gnews = executor.submit(fetch_gnews, query)
        f_newsdata = executor.submit(fetch_newsdata, query)
        f_tavily = executor.submit(fetch_tavily, query)
        f_rss = executor.submit(fetch_google_news_rss, query)

    # 1. Collect Search Context (PAA, Related)
    search_context = {"results": [], "paa": [], "related": []}
    try:
        s_data = f_serper.result()
        if not s_data["results"]:
            s_data = f_searchapi.result()
        search_context = s_data
    except: pass

    # 2. Collect News Signals
    news_results = []
    try: news_results.extend(f_rss.result() or [])
    except: pass
    try: news_results.extend(f_tavily.result() or [])
    except: pass
    try: news_results.extend(f_gnews.result() or [])
    except: pass
    try: news_results.extend(f_newsdata.result() or [])
    except: pass

    # Combine with search results
    news_results.extend(search_context["results"])

    return {
        "news": news_results,
        "paa": search_context["paa"],
        "related": search_context["related"]
    }

def fetch_all_news(query):
    """Run parallel fetch across all sources with aggressive redundancy."""
    logger.info(f"🚀 OSINT Engine active (Rotation Enabled) for: {query}")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_gnews = executor.submit(fetch_gnews, query)
        f_newsdata = executor.submit(fetch_newsdata, query)
        f_tavily = executor.submit(fetch_tavily, query)
        f_rss = executor.submit(fetch_google_news_rss, query)

    results = []
    # Prioritize RSS and Tavily for high-fidelity
    try: results.extend(f_rss.result() or [])
    except: pass
    try: results.extend(f_tavily.result() or [])
    except: pass
    try: results.extend(f_gnews.result() or [])
    except: pass
    try: results.extend(f_newsdata.result() or [])
    except: pass

    # Absolute fallback
    if len(results) < 3:
        logger.warning(f"⚠️ Low signal density for '{query}'. Triggering DuckDuckGo fallback.")
        results.extend(fetch_duckduckgo(query))

    return results

def clean_news(raw_news):
    """Deduplicate and strictly validate news items (Title + URL required)."""
    seen = set()
    clean = []

    if not raw_news:
        return []

    for item in raw_news:
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        
        if not title or not url or len(title) < 15 or url == "#":
            continue
        
        # Normalize title for uniqueness
        norm_title = re.sub(r'[^a-zA-Z0-9]', '', title.lower())
        
        if norm_title in seen:
            continue

        seen.add(norm_title)
        clean.append({
            "title": title,
            "source": item.get("source", "Verified Intel"),
            "url": url
        })

    return clean[:10]

def detect_trends(news):
    """Extract trending keywords from news titles."""
    words = []
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "to", "for", "at", "and", "on", "of", "with", "from", "report", "news", "says", "update"}
    
    for n in news:
        # Simple split and clean
        cleaned_words = [re.sub(r'[^a-z]', '', w.lower()) for w in n["title"].split()]
        words.extend([w for w in cleaned_words if w and w not in stop_words and len(w) > 3])

    common = Counter(words).most_common(5)
    return [word for word, count in common if count > 1] or ["global", "strategic", "monitoring"]

def get_evidence_block(news):
    """Refined evidence block extraction (Source-First)."""
    evidence = []
    sources = []

    for n in news:
        title = n.get("title")
        url = n.get("url")

        if not title or not url:
            continue

        evidence.append(f"- {title}")
        sources.append({
            "title": title,
            "url": url,
            "source": n.get("source", "Intel Source")
        })

    return "\n".join(evidence), sources

def build_reasoning(news):
    """Real Reasoning Engine: News -> Meaning -> Impact."""
    reasoning = []
    for n in news[:5]:
        reasoning.append({
            "headline": n["title"],
            "analysis": f"This indicates that {n['title'].lower()} is impacting current developments."
        })
    return reasoning

def get_real_time_evidence(query):
    """v2 Entry Point: Returns full intelligence context for the multi-step reasoning pipeline."""
    intel = fetch_all_intelligence(query)
    clean = clean_news(intel["news"])

    if not clean:
        return None, [], [], [], [], []

    evidence_str, sources = get_evidence_block(clean)
    reasoning_chain = build_reasoning(clean)
    trends = detect_trends(clean)
    
    # v2 returns: evidence, sources, reasoning, trends, paa, related
    return evidence_str, sources, reasoning_chain, trends, intel["paa"], intel["related"]
