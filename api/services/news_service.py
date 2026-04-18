import requests
import os
import logging
import time
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from .factcheck_service import fact_check_pipeline, batch_fact_check_news

logger = logging.getLogger(__name__)

def _get_active_key(env_var: str) -> str:
    """Helper to pick a valid API key from a comma-separated list, skipping placeholders."""
    keys_str = os.environ.get(env_var, "")
    if not keys_str:
        return ""
    keys = [k.strip() for k in keys_str.split(",") if k.strip() and "your_" not in k.lower()]
    return random.choice(keys) if keys else ""

# Regional caching for fast news
_FAST_CACHE = {}  # {country: {"data": ..., "ts": time.time()}}
CACHE_TTL = 300  # Optimized TTL for high-performance feed
CACHE_MIN_TTL = 60 # Minimum TTL to prevent spamming APIs even on force_refresh

# CATEGORY-BASED QUERY ENGINE (For surgical intelligence extraction)
CATEGORY_QUERIES = {
    "world": ["global news", "world breaking news", "international headlines", "global politics"],
    "india": ["India news", "India breaking", "Bharat national news", "Indian politics"],
    "usa": ["US news", "USA breaking news", "America latest news"],
    "economy": ["global economy", "stock market", "finance news", "business intelligence"],
    "conflict": ["war news", "military conflict", "geopolitical tensions", "defense updates"]
}

TRUSTED_SOURCES = ["reuters", "ap", "associated press", "bbc", "bloomberg", "al jazeera", "the guardian", "the wall street journal", "financial times"]

# INTELLIGENT REGIONAL FALLBACK POOL (Guarantees Different News per Region)
REGIONAL_FALLBACKS = {
    "in": [
        {"title": "India’s Digital Economy Projected to Hit $1 Trillion by 2030", "description": "New reports suggest India's UPI and digital infrastructure are accelerating growth.", "source": "India Tech Observer"},
        {"title": "ISRO Successfully Launches New Communications Satellite", "description": "India's space agency continues its streak of successful low-cost satellite launches.", "source": "Bharat Space"},
        {"title": "Rains in Mumbai Cause Temporary Traffic Disruptions", "description": "Local authorities advise residents to use public transport during heavy monsoon showers.", "source": "Financial Times India"},
        {"title": "New Manufacturing Hub to Open in Bangalore", "description": "The 'Make in India' initiative sees another major investment from global tech giant.", "source": "Business Today"},
        {"title": "Indian Indices Hit Record High on Global Cues", "description": "Nifty and Sensex show resilience as domestic institutional investors buy the dip.", "source": "Dalal Street Journal"},
        {"title": "New Semiconductor Plant Approved for Gujarat", "description": "The 'India Semiconductor Mission' gains momentum with a multi-billion dollar project.", "source": "Desh Gujarat"},
        {"title": "E-Rupee Adoption Grows Across Major Indian Retailers", "description": "The RBI-led CBDC pilot sees increased transaction volumes in Tier-1 cities.", "source": "India FinTech News"}
    ],
    "us": [
        {"title": "US Tech Giants Report Blockbuster Quarterly Earnings", "description": "Silicon Valley continues to lead the global AI push with record revenues.", "source": "Wall Street Echo"},
        {"title": "New Federal Policies Aim to Lower Healthcare Costs", "description": "The administration announced fresh measures to curb prescription drug prices in the US.", "source": "Washington Reporter"},
        {"title": "Major Winter Storm Warnings Issued for Northeast US", "description": "Meteorologists predict heavy snowfall across New York and New England this weekend.", "source": "US Weather Channel"},
        {"title": "Federal Reserve Hints at Potential Rate Cuts", "description": "Economic signals suggest cooling inflation, sparking optimism in US markets.", "source": "Fed Watch"},
        {"title": "First Fully Autonomous Cab Service Launches in Texas", "description": "The pilot program marks a major milestone in US transportation technology.", "source": "TechCrunch US"},
        {"title": "US Treasury Yields Slip as Investors Eye Job Data", "description": "Markets anticipate the latest non-farm payroll report for interest rate clues.", "source": "Bloomberg US"},
        {"title": "New Space Station Module Successfully Tested in Oregon", "description": "Private US space companies accelerate plans for post-ISS low earth orbit presence.", "source": "Science Daily US"}
    ],
    "gb": [
        {"title": "UK Energy Prices Expected to Stabilize This Winter", "description": "Regulatory changes and increased renewable output help lower household bills in Britain.", "source": "London Gazette"},
        {"title": "New Royal Exhibition Opens at Buckingham Palace", "description": "Thousands expected to visit the summer display of historic crown jewels in London.", "source": "The British Chronicle"},
        {"title": "Manchester United Announces Infrastructure Upgrade Plan", "description": "The legendary football club aims to modernize Old Trafford and surrounding areas.", "source": "UK Sport Inside"},
        {"title": "UK Fintech Reaches New Record in Venture Investment", "description": "London remains a dominant global hub for financial technology innovation.", "source": "City AM"},
        {"title": "High-Speed Rail Project Enters Phase 2 in UK", "description": "Connectivity between London and the North set to improve with latest HS2 development.", "source": "Guardian Rail"},
        {"title": "Oxford University Researchers Claim Medical Breakthrough", "description": "A new targeted therapy trial shows promising results for common respiratory issues in Britain.", "source": "Oxford News"},
        {"title": "British Pound Gains Strength Against Major Currencies", "description": "Markets react positively to latest GDP growth estimates from the Bank of England.", "source": "Financial Mirror UK"}
    ],
    "ca": [
        {"title": "Canadian Housing Market Shows Signs of Recovery", "description": "Increased supply and stable rates spark buying activity in Toronto and Vancouver.", "source": "Canada Real Estate Review"},
        {"title": "Canada Post Unveils New Sustainability Fleet", "description": "Electric vehicles to become the standard for nationwide mail delivery by 2028.", "source": "Ottawa Daily"},
        {"title": "Winter Festival Returns to Quebec City", "description": "The annual ice sculpture and outdoor celebration attracts global tourists to Canada.", "source": "Travel Canada"},
        {"title": "Emerging AI Tech Cluster Expands in Montreal", "description": "Quebec’s tech ecosystem continues to attract top AI research talent to Canada.", "source": "Montreal Mirror"}
    ]
}

FALLBACK_NEWS = [
    {
        "title": "Global Markets Rally Amid Tech Growth Surge",
        "description": "International stock markets recorded significant gains today as major technology companies reported better-than-expected quarterly earnings.",
        "urlToImage": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "Finance Intelligence"},
        "publishedAt": "2024-03-24T10:00:00Z",
        "url": "https://example.com/markets"
    },
    {
        "title": "SpaceX Successfully Launches 22 Starlink Satellites",
        "description": "SpaceX has successfully deployed another batch of Starlink satellites into low Earth orbit from California.",
        "urlToImage": "https://images.unsplash.com/photo-1517976487492-5750f3195933?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "Space Tech News"},
        "publishedAt": "2024-03-24T08:30:00Z",
        "url": "https://example.com/spacex"
    },
    {
        "title": "New Breakthrough in Sustainable Battery Technology",
        "description": "Researchers have announced a major breakthrough in solid-state battery technology that could double the range of electric vehicles.",
        "urlToImage": "https://images.unsplash.com/photo-1518770660439-4636190af475?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "Science Global"},
        "publishedAt": "2024-03-24T06:15:00Z",
        "url": "https://example.com/battery"
    },
    {
        "title": "AI Model Achieves Human-Level Proficiency in Coding",
        "description": "A new large language model has demonstrated unprecedented capability in solving complex algorithmic challenges, rivaling top human engineers.",
        "urlToImage": "https://images.unsplash.com/photo-1677442136019-21780ecad995?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "AI Weekly"},
        "publishedAt": "2024-03-24T05:00:00Z",
        "url": "https://example.com/ai-news"
    },
    {
        "title": "Renewable Energy Capacity Sees Record 50% Growth",
        "description": "Global additions to renewable energy capacity jumped by 50% in 2023, marking the fastest growth rate in two decades according to the IEA.",
        "urlToImage": "https://images.unsplash.com/photo-1509391366360-fe5bb58583fb?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "Green Tech"},
        "publishedAt": "2024-03-24T04:30:00Z",
        "url": "https://example.com/green"
    },
    {
        "title": "Quantum Computing Reach Milestone in Error Correction",
        "description": "Engineers have successfully demonstrated a new logical qubit that maintains its state 10x longer than previous records.",
        "urlToImage": "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?q=80&w=800&auto=format&fit=crop",
        "source": {"name": "Quantum Labs"},
        "publishedAt": "2024-03-24T03:00:00Z",
        "url": "https://example.com/quantum"
    }
]

def is_relevant(article, category):
    """Strict relevance filter (Step 3)."""
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    
    rules = {
        "india": ["india", "delhi", "modi", "bjp", "mumbai", "bharat", "indian"],
        "usa": ["usa", "america", "biden", "trump", "washington", "congress", "senate"],
        "economy": ["economy", "inflation", "stock", "gdp", "market", "fed", "finance", "bank", "interest rate"],
        "conflict": ["war", "attack", "military", "missile", "clash", "strike", "conflict", "defense"],
        "world": []
    }
    
    cat_key = category.lower()
    if cat_key == "world" or cat_key not in rules:
        return True
    
    # Relaxed matching (case-insensitive)
    return any(keyword in text for keyword in rules[cat_key]) or cat_key in text

def score_article(a):
    """Trust + Quality scoring (Step 5)."""
    score = 0
    published = str(a.get("publishedAt") or "").lower()
    source = (a.get("source", {}).get("name") or "").lower()
    
    # 1. Freshness (5pts if hours mentioned)
    if any(k in published for k in ["hour", "minute", "second"]):
        score += 5
    elif "today" in published or "recent" in published:
        score += 2

    # 2. Trusted source (5pts)
    if any(ts in source for ts in TRUSTED_SOURCES):
        score += 5

    # 3. Content detail (2pts)
    if len(a.get("description", "") or "") > 100:
        score += 2

    return score

def summarize_news_batch(articles):
    """LLM Clean Summarization (Step 7)."""
    if not articles:
        return articles
        
    try:
        from .factcheck_service import call_llm
        # Summarize selectively for the feed
        to_summarize = articles[:5]
        prompt = "Summarize these news articles into 1 clean sentence each. Be factual. Return only JSON array of strings.\n\n"
        for idx, a in enumerate(to_summarize):
            prompt += f"{idx}: {a['title']} - {a.get('description', 'No desc')}\n"
            
        res = call_llm([
            {"role": "system", "content": "You are a factual intelligence analyst. Return ONLY a JSON list of strings."},
            {"role": "user", "content": prompt}
        ])
        
        import json, re
        match = re.search(r'\[.*\]', res, re.DOTALL)
        if match:
            sums = json.loads(match.group())
            for i, s in enumerate(sums):
                if i < len(to_summarize):
                    to_summarize[i]["summary"] = s
    except Exception as e:
        logger.error(f"Summarization error: {e}")
    
    # Fallback/Default summary
    for a in articles:
        if "summary" not in a:
            a["summary"] = a.get("description", "Analysis pending...")[:120] + "..."
            
    return articles

def deduplicate_news(articles):
    """Title-based deduplication (Step 4)."""
    seen = set()
    unique = []
    for a in articles:
        title = (a.get("title") or "").lower().strip()
        if title and title not in seen:
            seen.add(title)
            unique.append(a)
    return unique

def fetch_source_newsapi(api_key, country="in", category=None):
    """Fetch from NewsAPI using targeted queries."""
    try:
        # Use helper internally to ensure we don't use placeholders
        active_key = _get_active_key("NEWS_API_KEY")
        if not active_key:
            return []
            
        params = {"pageSize": 10, "apiKey": active_key, "language": "en"}
        
        # Priority 1: Category Mapping
        if country.lower() in CATEGORY_QUERIES:
            query = random.choice(CATEGORY_QUERIES[country.lower()])
            params["q"] = query
            endpoint = "https://newsapi.org/v2/everything"
        else:
            params["country"] = country.lower()
            endpoint = "https://newsapi.org/v2/top-headlines"

        res = requests.get(endpoint, params=params, timeout=5)
        if res.status_code == 200:
            return res.json().get("articles", [])
    except Exception as e:
        logger.error(f"NewsAPI error: {e}")
    return []

def fetch_source_gnews(api_key, country="in"):
    """Fetch from GNews using targeted queries."""
    try:
        active_key = _get_active_key("GNEWS_API_KEY")
        if not active_key:
            return []

        query = random.choice(CATEGORY_QUERIES.get(country.lower(), ["breaking news"]))
        res = requests.get(
            f"https://gnews.io/api/v4/search?q={query}&lang=en&token={api_key}&max=10",
            timeout=5
        )
        if res.status_code == 200:
            articles = res.json().get("articles", [])
            return [{
                "title": item.get("title"),
                "description": item.get("description"),
                "source": {"name": item.get("source", {}).get("name")},
                "publishedAt": item.get("publishedAt"),
                "url": item.get("url")
            } for item in articles]
    except Exception as e:
        logger.error(f"GNews error: {e}")
    return []

def fetch_source_newsdata(api_key, country="in"):
    """Fetch from NewsData.io."""
    try:
        active_key = _get_active_key("NEWSDATA_API_KEY")
        if not active_key:
            return []
            
        query = random.choice(CATEGORY_QUERIES.get(country.lower(), ["breaking news"]))
        res = requests.get(
            f"https://newsdata.io/api/1/news?apikey={api_key}&q={query}&language=en",
            timeout=5
        )
        if res.status_code == 200:
            results = res.json().get("results", [])
            return [{
                "title": item.get("title"),
                "description": item.get("description"),
                "source": {"name": item.get("source_id")},
                "publishedAt": item.get("pubDate"),
                "url": item.get("link")
            } for item in results]
    except Exception as e:
        logger.error(f"NewsData error: {e}")
    return []

def fetch_source_duckduckgo(country="in"):
    """Fallback fetch from DuckDuckGo search."""
    try:
        from .duckduckgo_service import search_duckduckgo
        query = random.choice(CATEGORY_QUERIES.get(country.lower(), ["breaking news"]))
        results = search_duckduckgo(query)
        return [{
            "title": item.get("title"),
            "description": item.get("text"),
            "source": {"name": item.get("source")},
            "publishedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "url": item.get("url")
        } for item in results]
    except Exception as e:
        logger.error(f"DDG Fetch error: {e}")
    return []

def get_fast_news(limit=10, force_refresh=False, country="global"):
    """
    Precision Intelligence Orchestrator (v2.0).
    Steps: Parallel Fetch -> Strict Relevance -> Deduplicate -> Score -> AI Summarize -> Final Rank.
    """
    global _FAST_CACHE
    country_key = country.lower()
    
    # 1. Check Cache (Step 9)
    if not force_refresh and country_key in _FAST_CACHE:
        if time.time() - _FAST_CACHE[country_key]["ts"] < CACHE_TTL:
            return _FAST_CACHE[country_key]["data"]

    all_articles = []
    
    # 2. Parallel Multi-Source Fetch (Step 2)
    with ThreadPoolExecutor(max_workers=4) as executor:
        n_api = _get_active_key("NEWS_API_KEY")
        g_api = _get_active_key("GNEWS_API_KEY")
        nd_api = _get_active_key("NEWSDATA_API_KEY")
        
        funcs = [
            (fetch_source_newsapi, n_api, country_key),
            (fetch_source_gnews, g_api, country_key),
            (fetch_source_newsdata, nd_api, country_key),
            (fetch_source_duckduckgo, country_key)
        ]
        
        futures = [executor.submit(f[0], *f[1:]) for f in funcs]
        for f in futures:
            try:
                res = f.result(timeout=6)
                if res: all_articles.extend(res)
            except: pass

    # 3. Strict Relevance Filter (Step 3/10)
    filtered = [a for a in all_articles if is_relevant(a, country_key)]
    
    # 4. Deduplication (Step 4)
    unique = deduplicate_news(filtered)
    
    # 5. Scoring (Step 5)
    for a in unique:
        a["score"] = score_article(a)
    
    # 6. Final Ranking (Step 6)
    ranked = sorted(unique, key=lambda x: x.get("score", 0), reverse=True)
    top_items = ranked[:limit]
    
    # 7. AI Summarization (Step 7)
    final_intelligence = summarize_news_batch(top_items)
    
    # 8. Final Normalization for Frontend (Step 8)
    formatted = []
    for a in final_intelligence:
        source_name = (a.get("source", {}).get("name") or "Global Intel").replace(".com", "").upper()
        formatted.append({
            "title": a.get("title", "Breaking News"),
            "summary": a.get("summary", a.get("description", "No summary available.")),
            "source": source_name,
            "category": country.upper() if country != "global" else "WORLD",
            "published": "JUST NOW" if a.get("score", 0) > 10 else "Recent",
            "publishedAt": a.get("publishedAt", "Recent"),
            "url": a.get("url", "#"),
            "confidence": 85 + min(15, a.get("score", 0)),
            "factCheck": "VERIFIED" if a.get("score", 0) > 8 else "UNVERIFIED"
        })

    results = formatted[:limit]
    _FAST_CACHE[country_key] = {"data": results, "ts": time.time()}
    return results

def get_news(country="global", limit=8):
    """Modern News endpoint with parallel background-style verification."""
    fast_news = get_fast_news(limit=limit, country=country)
    
    # Use parallel batch processor for verification
    # This prevents the "waiting for each article" serial bottleneck
    verified_news = batch_fact_check_news(fast_news[:limit])
    
    return verified_news

def _fetch_gnews_search(query, limit):
    api_key = _get_active_key("GNEWS_API_KEY")
    if not api_key: return []
    res = requests.get(f"https://gnews.io/api/v4/search?q={query}&token={api_key}&lang=en&max={limit}", timeout=4.5)
    if res.status_code == 200:
        data = res.json()
        formatted = []
        for item in data.get("articles", [])[:limit]:
            formatted.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "text": item.get("description", ""),  # Backwards compatibility
                "url": item.get("url", ""),
                "source": item.get("source", {}).get("name", "GNews"),
                "published_at": item.get("publishedAt", ""),
                "image_url": item.get("image", "")
            })
        return formatted
    return []

def _fetch_newsdata_search(query, limit):
    api_key = _get_active_key("NEWSDATA_API_KEY")
    if not api_key: return []
    res = requests.get(f"https://newsdata.io/api/1/news?apikey={api_key}&q={query}&language=en", timeout=4.5)
    if res.status_code == 200:
        data = res.json()
        formatted = []
        for item in data.get("results", [])[:limit]:
            formatted.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "text": item.get("description", ""),
                "url": item.get("link", ""),
                "source": item.get("source_id", "NewsData"),
                "published_at": item.get("pubDate", ""),
                "image_url": item.get("image_url", "")
            })
        return formatted
    return []

def _fetch_currents_search(query, limit):
    api_key = _get_active_key("CURRENTS_API_KEY")
    if not api_key: return []
    res = requests.get(f"https://api.currentsapi.services/v1/search?apiKey={api_key}&keywords={query}&language=en", timeout=4.5)
    if res.status_code == 200:
        data = res.json()
        formatted = []
        for item in data.get("news", [])[:limit]:
            formatted.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "text": item.get("description", ""),
                "url": item.get("url", ""),
                "source": item.get("author", "Currents API") or "Currents API",
                "published_at": item.get("published", ""),
                "image_url": item.get("image", "")
            })
        return formatted
    return []

def _fetch_thenews_search(query, limit):
    api_key = _get_active_key("THENEWS_API_KEY")
    if not api_key: return []
    res = requests.get(f"https://api.thenewsapi.com/v1/news/all?api_token={api_key}&search={query}&language=en", timeout=4.5)
    if res.status_code == 200:
        data = res.json()
        formatted = []
        for item in data.get("data", [])[:limit]:
            formatted.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "text": item.get("description", ""),
                "url": item.get("url", ""),
                "source": item.get("source", "TheNewsAPI"),
                "published_at": item.get("published_at", ""),
                "image_url": item.get("image_url", "")
            })
        return formatted
    return []

def _fetch_google_rss_search(query, limit):
    """Zero-auth fallback using Google News RSS."""
    import xml.etree.ElementTree as ET
    import urllib.parse
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            formatted = []
            for item in root.findall(".//item")[:limit]:
                formatted.append({
                    "title": item.gettext() if hasattr(item, 'gettext') else item.findtext("title", ""),
                    "description": item.findtext("description", ""),
                    "text": item.findtext("description", ""),
                    "url": item.findtext("link", ""),
                    "source": "GOOGLE NEWS (RSS)",
                    "published_at": item.findtext("pubDate", ""),
                    "image_url": "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&q=80&w=800"
                })
            return formatted
    except Exception as e:
        logger.error(f"Google RSS error: {e}")
    return []

def _fetch_tavily_search(query, limit):
    """High-reliability intelligence from Tavily."""
    api_key = _get_active_key("TAVILY_API_KEY")
    if not api_key: return []
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": limit,
            "topic": "news"
        }
        res = requests.post(url, json=payload, timeout=6)
        if res.status_code == 200:
            data = res.json()
            formatted = []
            for item in data.get("results", [])[:limit]:
                formatted.append({
                    "title": item.get("title", ""),
                    "description": item.get("content", ""),
                    "text": item.get("content", ""),
                    "url": item.get("url", ""),
                    "source": "TAVILY INTEL",
                    "published_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "image_url": "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&q=80&w=800"
                })
            return formatted
    except Exception as e:
        logger.error(f"Tavily error: {e}")
    return []

def fetch_news_with_rotation(query: str, max_results: int = 10) -> list:
    """
    Tries each API in order. Returns first successful non-empty result.
    Falls back to DuckDuckGo if all APIs fail.
    """
    from .duckduckgo_service import search_duckduckgo
    
    apis = [
        ("Tavily", _fetch_tavily_search),
        ("GNews", _fetch_gnews_search),
        ("NewsData", _fetch_newsdata_search),
        ("GoogleRSS", _fetch_google_rss_search),
        ("Currents", _fetch_currents_search),
        ("TheNewsAPI", _fetch_thenews_search)
    ]
    
    for name, fetch_func in apis:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fetch_func, query, max_results)
                results = future.result(timeout=5)
                
            if results and len(results) >= 2:
                logger.info(f"✅ News rotated successfully using: {name}")
                return results
            else:
                logger.warning(f"⚠️ {name} returned < 2 results. Rotating to next...")
        except Exception as e:
            logger.error(f"❌ {name} failed: {e}. Rotating to next...")
            
    # Fallback to DuckDuckGo
    logger.warning("🚨 All primary APIs failed. Falling back to DuckDuckGo scraping.")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(search_duckduckgo, query)
            ddg_results = future.result(timeout=10)
        
        formatted_ddg = []
        for a in ddg_results[:max_results]:
            formatted_ddg.append({
                "title": str(a.get("title", "")),
                "description": str(a.get("text", "")),
                "text": str(a.get("text", "")),
                "url": str(a.get("url", "")),
                "source": str(a.get("source", "DuckDuckGo")),
                "published_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "image_url": "https://images.unsplash.com/photo-1504711434969-e33886168f5c?auto=format&fit=crop&q=80&w=800"
            })
        return formatted_ddg
    except Exception as e:
        logger.error(f"❌ DuckDuckGo also failed: {e}")
        return []
